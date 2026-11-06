/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * OBJ material libraries, COLLADA images and 3DS/FBX textures used to be requested by URL from the
 * site root, which the web origin answers with 403. These tests pin that each format's referenced
 * files are found, downloaded from the asset through the resolver, and handed to the loader as
 * blob URLs, and that the loader never receives a URL that would leave the viewer.
 */

import { AssetDependencyResolver, PLACEHOLDER_IMAGE_DATA_URL } from "./assetDependencyResolver";
import {
    combineMaterialCreators,
    extractColladaImageRefs,
    extractMtlTextureRefs,
    extractObjMaterialLibraries,
    loadWithDependencies,
    USD_NO_GEOMETRY_MESSAGE,
} from "./dependencyLoading";
import { loadFile } from "./fileLoaders";

jest.mock("./fileLoaders", () => ({
    ...jest.requireActual("./fileLoaders"),
    loadFile: jest.fn(),
}));

const mockedLoadFile = loadFile as jest.MockedFunction<typeof loadFile>;
const encoder = new TextEncoder();

class FakeLoadingManager {
    private modifier: (url: string) => string = (url) => url;
    setURLModifier(modifier: (url: string) => string) {
        this.modifier = modifier;
        return this;
    }
    resolveURL(url: string) {
        return this.modifier(url);
    }
}

function installFetch(files: Record<string, string>): string[] {
    const requested: string[] = [];
    global.fetch = jest.fn(async (url: string) => {
        const key = decodeURIComponent(url.replace(/^.*\/download\/stream\//, ""));
        requested.push(key);
        if (files[key] === undefined) {
            return { ok: false, status: 403, headers: { get: (): string | null => null } };
        }
        return {
            ok: true,
            status: 200,
            headers: { get: (): string | null => null },
            arrayBuffer: () => Promise.resolve(encoder.encode(files[key]).buffer),
        };
    }) as any;
    return requested;
}

function newResolver() {
    return new AssetDependencyResolver({
        assetId: "asset-1",
        databaseId: "db-1",
        apiEndpoint: "https://api.example.test/",
        getAuthHeader: () => Promise.resolve("Bearer t"),
    });
}

const buffer = (value: string) => encoder.encode(value).buffer as ArrayBuffer;

/** A parsed scene: a root group holding one child node with the given flags. */
function sceneWith(child: Record<string, any>) {
    return {
        isGroup: true,
        traverse(fn: (node: any) => void) {
            fn(this);
            fn(child);
        },
    };
}

describe("reference scanners", () => {
    it("reads OBJ material libraries the way OBJLoader does", () => {
        expect(
            extractObjMaterialLibraries(
                "# cube\nmtllib cube.mtl\nv 0 0 0\n  mtllib  mats/My Lib.mtl \r\nmtllib cube.mtl\n"
            )
        ).toEqual(["cube.mtl", "mats/My Lib.mtl"]);
        expect(extractObjMaterialLibraries("v 0 0 0\nusemtl x\n")).toEqual([]);
    });

    it("reads MTL texture names with MTLLoader's option handling", () => {
        const mtl = [
            "newmtl wood",
            "map_Kd textures/wood diffuse.png",
            "map_Bump -bm 0.5 textures/wood_bump.png",
            "bump -s 2 2 1 -o 0 0 0 bump.jpg",
            "disp disp.png",
            "norm normal.png",
            "map_d alpha.png",
            "# map_Kd commented.png",
            "Kd 1 1 1",
        ].join("\n");

        expect(extractMtlTextureRefs(mtl)).toEqual([
            "textures/wood diffuse.png",
            "textures/wood_bump.png",
            "bump.jpg",
            "disp.png",
            "normal.png",
            "alpha.png",
        ]);
    });

    it("reads COLLADA 1.4 and 1.5 image init_from, not surface sources", () => {
        const dae = `<?xml version="1.0"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <library_images>
    <image id="img1"><init_from>textures/elf.png</init_from></image>
    <image id="img2"><init_from><ref>./skin.jpg</ref></init_from></image>
  </library_images>
  <library_effects><effect id="e"><profile_COMMON>
    <newparam sid="s"><surface type="2D"><init_from>img1</init_from></surface></newparam>
  </profile_COMMON></effect></library_effects>
</COLLADA>`;

        expect(extractColladaImageRefs(dae)).toEqual(["textures/elf.png", "./skin.jpg"]);
    });

    it("combines material libraries, first definition wins", () => {
        const a = { materialsInfo: { red: {} }, create: jest.fn(() => "a-red") };
        const b = { materialsInfo: { red: {}, blue: {} }, create: jest.fn((n) => `b-${n}`) };
        const combined = combineMaterialCreators([a, b]);

        expect(combined.create("red")).toBe("a-red");
        expect(combined.create("blue")).toBe("b-blue");
        expect(combined.create("none")).toBeUndefined();
        expect(combineMaterialCreators([a])).toBe(a);
    });
});

describe("loadWithDependencies", () => {
    const originalFetch = global.fetch;
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    let blobs = 0;

    beforeEach(() => {
        blobs = 0;
        URL.createObjectURL = jest.fn(() => `blob:test/${++blobs}`);
        URL.revokeObjectURL = jest.fn();
        mockedLoadFile.mockReset();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });

    afterEach(() => {
        global.fetch = originalFetch;
        URL.createObjectURL = originalCreate;
        URL.revokeObjectURL = originalRevoke;
        delete (window as any).THREEBundle;
        jest.restoreAllMocks();
    });

    function installBundle(extra: Record<string, any> = {}) {
        (window as any).THREEBundle = { THREE: { LoadingManager: FakeLoadingManager }, ...extra };
    }

    it("loads an OBJ's material library and its textures relative to the library", async () => {
        const requested = installFetch({
            "m/mats/car.mtl": "newmtl paint\nmap_Kd paint.png\nmap_Bump -bm 1 bump.png\n",
            "m/mats/paint.png": "PNG",
        });
        const textureUrls: string[] = [];
        class FakeMTLLoader {
            constructor(private manager: FakeLoadingManager) {}
            parse() {
                const manager = this.manager;
                return {
                    materialsInfo: { paint: {} },
                    preload() {
                        textureUrls.push(manager.resolveURL("paint.png"));
                        textureUrls.push(manager.resolveURL("bump.png"));
                    },
                    create: jest.fn(),
                };
            }
        }
        installBundle({ MTLLoader: FakeMTLLoader });
        mockedLoadFile.mockResolvedValue({ object: { name: "car" } });
        const resolver = newResolver();

        const result = await loadWithDependencies({
            arrayBuffer: buffer("mtllib mats/car.mtl\nv 0 0 0\n"),
            extension: "obj",
            fileKey: "m/car.obj",
            fileName: "car.obj",
            resolver,
        });

        expect(textureUrls).toEqual(["blob:test/1", PLACEHOLDER_IMAGE_DATA_URL]);
        expect(requested).toEqual(["m/mats/car.mtl", "m/mats/paint.png", "m/mats/bump.png"]);
        const options = mockedLoadFile.mock.calls[0][4];
        expect(options?.materials?.materialsInfo).toEqual({ paint: {} });
        expect(result.warnings).toEqual(["m/mats/bump.png (not found in this asset)"]);
        expect(result.dependencyCount).toBe(2);
    });

    it("loads an OBJ without materials when its library is missing", async () => {
        installFetch({});
        installBundle({ MTLLoader: jest.fn() });
        mockedLoadFile.mockResolvedValue({ object: {} });

        const result = await loadWithDependencies({
            arrayBuffer: buffer("mtllib gone.mtl\n"),
            extension: "obj",
            fileKey: "gone.obj",
            fileName: "gone.obj",
            resolver: newResolver(),
        });

        expect(mockedLoadFile.mock.calls[0][4]?.materials).toBeUndefined();
        expect(result.warnings).toEqual(["gone.mtl (not found in this asset)"]);
    });

    it("hands a COLLADA loader blob URLs for the images it names", async () => {
        installFetch({ "models/textures/elf.png": "PNG" });
        installBundle();
        let requestedUrl = "";
        mockedLoadFile.mockImplementation(async (_buffer, _ext, _name, manager) => {
            requestedUrl = manager.resolveURL("textures/elf.png");
            return { object: {} };
        });

        await loadWithDependencies({
            arrayBuffer: buffer(
                '<COLLADA><library_images><image id="i"><init_from>textures/elf.png</init_from></image></library_images></COLLADA>'
            ),
            extension: "dae",
            fileKey: "models/elf.dae",
            fileName: "elf.dae",
            resolver: newResolver(),
        });

        expect(requestedUrl).toBe("blob:test/1");
    });

    it("probes a 3DS file for the textures it requests, then parses it with them", async () => {
        const requested = installFetch({ "maps/portal.jpg": "JPG" });
        installBundle();
        const dispose = jest.fn();
        const urls: string[] = [];
        mockedLoadFile.mockImplementation(async (_buffer, _ext, _name, manager) => {
            urls.push(manager.resolveURL("maps/portal.jpg"));
            return {
                object: { traverse: (fn: any) => fn({ geometry: { dispose }, material: null }) },
            };
        });

        await loadWithDependencies({
            arrayBuffer: new ArrayBuffer(8),
            extension: "3ds",
            fileKey: "portal.3ds",
            fileName: "portal.3ds",
            resolver: newResolver(),
        });

        expect(mockedLoadFile).toHaveBeenCalledTimes(2);
        expect(urls).toEqual([PLACEHOLDER_IMAGE_DATA_URL, "blob:test/1"]);
        expect(requested).toEqual(["maps/portal.jpg"]);
        expect(dispose).toHaveBeenCalledTimes(1);
    });

    it("parses an FBX once when none of the textures it requests are in the asset", async () => {
        const requested = installFetch({});
        installBundle();
        const dispose = jest.fn();
        mockedLoadFile.mockImplementation(async (_buffer, _ext, _name, manager) => {
            manager.resolveURL("textures/skin.png");
            manager.resolveURL("https://example.com/eye.png");
            return {
                object: { traverse: (fn: any) => fn({ geometry: { dispose }, material: null }) },
            };
        });
        const resolver = newResolver();

        const result = await loadWithDependencies({
            arrayBuffer: new ArrayBuffer(8),
            extension: "fbx",
            fileKey: "m/dancer.fbx",
            fileName: "dancer.fbx",
            resolver,
        });

        expect(mockedLoadFile).toHaveBeenCalledTimes(1);
        expect(dispose).not.toHaveBeenCalled();
        expect(requested).toEqual(["m/textures/skin.png"]);
        expect(result.warnings).toEqual([
            "https://example.com/eye.png (external URL, not fetched)",
            "m/textures/skin.png (not found in this asset)",
        ]);
    });

    it("parses an FBX once when it requests no external files", async () => {
        installFetch({});
        installBundle();
        mockedLoadFile.mockImplementation(async (_buffer, _ext, _name, manager) => {
            manager.resolveURL("blob:embedded-texture");
            return { object: {} };
        });

        await loadWithDependencies({
            arrayBuffer: new ArrayBuffer(8),
            extension: "fbx",
            fileKey: "a.fbx",
            fileName: "a.fbx",
            resolver: newResolver(),
        });

        expect(mockedLoadFile).toHaveBeenCalledTimes(1);
    });

    it("packages a USD layer's references before loading it", async () => {
        installFetch({ "part.usda": "#usda 1.0\n" });
        const zipSync = jest.fn(() => new Uint8Array([0x50, 0x4b, 0x03, 0x04]));
        installBundle({ fflate: { zipSync, unzipSync: jest.fn() } });
        mockedLoadFile.mockResolvedValue({ object: sceneWith({ isMesh: true }) });

        await loadWithDependencies({
            arrayBuffer: buffer("prepend references = @./part.usda@"),
            extension: "usda",
            fileKey: "scene.usda",
            fileName: "scene.usda",
            resolver: newResolver(),
        });

        expect(Object.keys((zipSync.mock.calls[0] as any)[0])).toEqual(["scene.usda", "part.usda"]);
        expect(new Uint8Array(mockedLoadFile.mock.calls[0][0])[0]).toBe(0x50);
    });

    it("says so when a USD scene composes nothing to draw, instead of a blank canvas", async () => {
        installFetch({ "geo/teapot.usd": "#usda 1.0\n" });
        const zipSync = jest.fn(() => new Uint8Array([0x50, 0x4b, 0x03, 0x04]));
        installBundle({ fflate: { zipSync, unzipSync: jest.fn() } });
        const dispose = jest.fn();
        mockedLoadFile.mockResolvedValue({
            object: sceneWith({ isGroup: true, geometry: { dispose } }),
        });

        await expect(
            loadWithDependencies({
                arrayBuffer: buffer("payload = @geo/teapot.usd@"),
                extension: "usd",
                fileKey: "Teapot.usd",
                fileName: "Teapot.usd",
                resolver: newResolver(),
            })
        ).rejects.toThrow(USD_NO_GEOMETRY_MESSAGE);
        expect(dispose).toHaveBeenCalledTimes(1);
    });

    it("carries the sublayers it did not load with the no-geometry message", async () => {
        const requested = installFetch({ "layers/geo.usda": "#usda 1.0\n" });
        installBundle({ fflate: { zipSync: jest.fn(), unzipSync: jest.fn() } });
        mockedLoadFile.mockResolvedValue({ object: sceneWith({ isGroup: true }) });

        const failure = await loadWithDependencies({
            arrayBuffer: buffer("#usda 1.0\n(\n    subLayers = [@layers/geo.usda@]\n)\n"),
            extension: "usda",
            fileKey: "scene.usda",
            fileName: "scene.usda",
            resolver: newResolver(),
        }).catch((error) => error);

        expect(failure.message).toBe(USD_NO_GEOMETRY_MESSAGE);
        expect(failure.warnings).toEqual([
            "layers/geo.usda (not shown by this viewer (USD sublayers))",
        ]);
        expect(requested).toEqual([]);
    });

    it("keeps USD scenes that hold points or lines, and other formats without meshes", async () => {
        installFetch({});
        installBundle({ fflate: { zipSync: jest.fn(), unzipSync: jest.fn() } });

        mockedLoadFile.mockResolvedValue({ object: sceneWith({ isPoints: true }) });
        await expect(
            loadWithDependencies({
                arrayBuffer: buffer("#usda 1.0\n"),
                extension: "usda",
                fileKey: "cloud.usda",
                fileName: "cloud.usda",
                resolver: newResolver(),
            })
        ).resolves.toBeDefined();

        mockedLoadFile.mockResolvedValue({ object: sceneWith({ isGroup: true }) });
        await expect(
            loadWithDependencies({
                arrayBuffer: new ArrayBuffer(8),
                extension: "glb",
                fileKey: "empty.glb",
                fileName: "empty.glb",
                resolver: newResolver(),
            })
        ).resolves.toBeDefined();
    });

    it("rewrites glTF URIs to blob URLs", async () => {
        installFetch({ "m/buf.bin": "BIN" });
        installBundle();
        mockedLoadFile.mockResolvedValue({ object: {} });

        const result = await loadWithDependencies({
            arrayBuffer: buffer(JSON.stringify({ buffers: [{ uri: "buf.bin" }] })),
            extension: "gltf",
            fileKey: "m/a.gltf",
            fileName: "a.gltf",
            resolver: newResolver(),
        });

        const passed = JSON.parse(new TextDecoder().decode(mockedLoadFile.mock.calls[0][0]));
        expect(passed.buffers[0].uri).toBe("blob:test/1");
        expect(result.warnings).toBeUndefined();
        expect(result.dependencyCount).toBe(1);
    });

    it("loads self-contained formats through an isolating manager without downloads", async () => {
        const requested = installFetch({});
        installBundle();
        let answer = "";
        mockedLoadFile.mockImplementation(async (_buffer, _ext, _name, manager) => {
            answer = manager.resolveURL("textures/anything.png");
            return { object: {} };
        });

        await loadWithDependencies({
            arrayBuffer: new ArrayBuffer(8),
            extension: "glb",
            fileKey: "a.glb",
            fileName: "a.glb",
            resolver: newResolver(),
        });

        expect(answer).toBe(PLACEHOLDER_IMAGE_DATA_URL);
        expect(requested).toEqual([]);
    });
});
