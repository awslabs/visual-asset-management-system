/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The OCCT loader opens STEP, IGES and BREP files. These tests pin that `.igs` opens like `.iges`,
 * that a file OCCT cannot read (`success: false`, no meshes) is reported with a short message
 * instead of a script error, and that meshes without geometry are left out, a result with none left
 * failing with a message instead of opening a blank canvas (#1734).
 */

import {
    LoadBrep,
    LoadIges,
    LoadStep,
    isDrawableOcctMesh,
    isOcctNoGeometryError,
    loadOCCTFile,
    occtNoGeometryMessage,
} from "./occtLoader";

const MESH = {
    attributes: { position: { array: [0, 0, 0, 1, 0, 0, 0, 1, 0] } },
    index: { array: [0, 1, 2] },
};

// What occt-import-js 0.0.23 returns for each part of a STEP assembly it reads but cannot tessellate.
const EMPTY_MESH = {
    name: "part",
    attributes: { position: { array: [] } },
    index: { array: [] },
};

class FakeObject3D {
    children: any[] = [];
    add(child: any) {
        this.children.push(child);
    }
}

const FakeTHREE = {
    Object3D: FakeObject3D,
    BufferGeometry: class {
        setAttribute = jest.fn();
        setIndex = jest.fn();
    },
    Float32BufferAttribute: class {},
    BufferAttribute: class {
        constructor(public array: any, public itemSize: number) {}
    },
    Color: class {},
    MeshPhongMaterial: class {},
    Mesh: class {
        constructor(public geometry: any, public material: any) {}
    },
};

let occt: {
    ReadStepFile: jest.Mock;
    ReadIgesFile: jest.Mock;
    ReadBrepFile: jest.Mock;
};

describe("occtLoader", () => {
    const originalFetch = global.fetch;

    beforeEach(() => {
        const read = () => jest.fn(() => ({ success: true, meshes: [MESH] }));
        occt = { ReadStepFile: read(), ReadIgesFile: read(), ReadBrepFile: read() };
        (window as any).THREEBundle = { occtimportjs: jest.fn(async () => occt) };
        global.fetch = jest.fn(async () => ({
            arrayBuffer: async () => new ArrayBuffer(4),
        })) as any;
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });

    afterEach(() => {
        global.fetch = originalFetch;
        delete (window as any).THREEBundle;
        jest.restoreAllMocks();
    });

    it("opens .igs files with the IGES reader, like .iges", async () => {
        const igs = await loadOCCTFile("blob:x", "cube-10x10.IGS", FakeTHREE);
        const iges = await loadOCCTFile("blob:y", "cube.iges", FakeTHREE);

        expect(occt.ReadIgesFile).toHaveBeenCalledTimes(2);
        expect(occt.ReadStepFile).not.toHaveBeenCalled();
        expect(igs.children).toHaveLength(1);
        expect(iges.children).toHaveLength(1);
    });

    const loaders: Array<[string, typeof LoadStep, keyof typeof occt]> = [
        ["STEP", LoadStep, "ReadStepFile"],
        ["IGES", LoadIges, "ReadIgesFile"],
        ["BREP", LoadBrep, "ReadBrepFile"],
    ];

    it.each(loaders)(
        "reports an unreadable %s file with a short message",
        async (format, load, reader) => {
            occt[reader].mockReturnValue({ success: false, meshes: undefined });
            await expect(load("blob:x", FakeTHREE)).rejects.toThrow(
                `The ${format} file could not be read.`
            );
            await expect(load("blob:x", FakeTHREE)).rejects.not.toThrow(/iterable/);
        }
    );

    it.each(loaders)(
        "reports %s results with no meshes as unreadable",
        async (format, load, reader) => {
            occt[reader].mockReturnValue({ success: true, meshes: [] });
            await expect(load("blob:x", FakeTHREE)).rejects.toThrow(
                `The ${format} file could not be read.`
            );
        }
    );

    it.each(loaders)(
        "reports a %s result whose meshes all have no geometry, suggesting a conversion",
        async (format, load, reader) => {
            occt[reader].mockReturnValue({
                success: true,
                meshes: Array.from({ length: 57 }, () => EMPTY_MESH),
            });
            const failure = load("blob:x", FakeTHREE);
            await expect(failure).rejects.toThrow(occtNoGeometryMessage(format));
            await expect(load("blob:x", FakeTHREE)).rejects.toThrow(
                /Convert it to a format such as GLB/
            );
        }
    );

    it("names the 3D Basic Conversion pipeline only for STEP, the CAD format it reads", () => {
        expect(occtNoGeometryMessage("STEP")).toMatch(/3D Basic Conversion pipeline/);
        expect(occtNoGeometryMessage("IGES")).not.toMatch(/3D Basic Conversion pipeline/);
        expect(occtNoGeometryMessage("BREP")).not.toMatch(/3D Basic Conversion pipeline/);
    });

    it.each(loaders)(
        "keeps only the %s meshes that have geometry when some are empty",
        async (_format, load, reader) => {
            occt[reader].mockReturnValue({
                success: true,
                meshes: [
                    EMPTY_MESH,
                    MESH,
                    { attributes: { position: { array: [0, 0, 0] } } },
                    MESH,
                ],
            });
            const object = await load("blob:x", FakeTHREE);
            expect(object.children).toHaveLength(2);
        }
    );

    it("treats a mesh without index data or with partial triangles as empty", () => {
        expect(isDrawableOcctMesh(MESH)).toBe(true);
        expect(isDrawableOcctMesh(EMPTY_MESH)).toBe(false);
        expect(isDrawableOcctMesh({ attributes: MESH.attributes })).toBe(false);
        expect(isDrawableOcctMesh({ ...MESH, index: { array: [0, 1] } })).toBe(false);
        expect(
            isDrawableOcctMesh({
                attributes: { position: { array: [0, 0, 0, 1, 0, 0, 0, 1] } },
                index: MESH.index,
            })
        ).toBe(false);
        expect(isDrawableOcctMesh(undefined)).toBe(false);
    });

    it("indexes a mesh past 65,535 vertices with 32-bit indices", async () => {
        const vertices = 70000;
        occt.ReadStepFile.mockReturnValue({
            success: true,
            meshes: [
                {
                    attributes: { position: { array: new Float32Array(vertices * 3) } },
                    index: { array: [0, 1, vertices - 1] },
                },
            ],
        });
        const object = await LoadStep("blob:x", FakeTHREE);
        const geometry = object.children[0].geometry;
        const index = geometry.setIndex.mock.calls[0][0];
        expect(index.array).toBeInstanceOf(Uint32Array);
        expect(Array.from(index.array)).toEqual([0, 1, vertices - 1]);
    });

    it("recognises the no-geometry message for every format, and nothing else", () => {
        for (const format of ["STEP", "IGES", "BREP"]) {
            expect(isOcctNoGeometryError(occtNoGeometryMessage(format))).toBe(true);
        }
        expect(isOcctNoGeometryError("The STEP file could not be read.")).toBe(false);
    });
});
