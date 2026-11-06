/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The OCCT loader opens STEP, IGES and BREP files. These tests pin that `.igs` opens like `.iges`,
 * and that a file OCCT cannot read (`success: false`, no meshes) is reported with a short message
 * instead of a script error.
 */

import { LoadBrep, LoadIges, LoadStep, loadOCCTFile } from "./occtLoader";

const MESH = {
    attributes: { position: { array: [0, 0, 0, 1, 0, 0, 0, 1, 0] } },
    index: { array: [0, 1, 2] },
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
    BufferAttribute: class {},
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
});
