/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The upload path rule (#1462, #1471): one rule for picked and dropped folders, evaluated over the
 * whole selection, with duplicates flagged rather than skipped.
 */

import {
    DUPLICATE_PATH_ERROR,
    INVALID_PATH_ERROR,
    PathContext,
    UploadRoot,
    looseReferencingFiles,
    markExisting,
    normalizeDestination,
    planUploadPaths,
    removeFromRoots,
    rootsFromInputFiles,
} from "./uploadPaths";

const f = (name: string, size = 1) => new File(["x".repeat(size)], name);

const folder = (name: string, paths: string[]): UploadRoot => ({
    kind: "folder",
    name,
    files: paths.map((p) => ({ handle: f(p.split("/").pop() as string), path: p })),
});
const loose = (name: string): UploadRoot => ({ kind: "file", name, handle: f(name) });

const create: PathContext = { flow: "createAsset", destination: "/", folderNameMode: "auto" };
const addAt = (destination: string): PathContext => ({
    flow: "addFiles",
    destination,
    folderNameMode: "auto",
});

const paths = (roots: UploadRoot[], ctx: PathContext) =>
    planUploadPaths(roots, ctx)
        .map((p) => p.finalPath)
        .sort();

describe("planUploadPaths", () => {
    it("Create Asset, a single folder: the folder name is dropped", () => {
        expect(paths([folder("pack", ["scene.usda", "props/pallet.usda"])], create)).toEqual([
            "/props/pallet.usda",
            "/scene.usda",
        ]);
    });

    it("Add Files at /scene/, a single folder: the folder keeps its name", () => {
        expect(paths([folder("textures", ["a.png"])], addAt("/scene/"))).toEqual([
            "/scene/textures/a.png",
        ]);
    });

    it("Add Files at /scene/, a loose file lands directly in /scene/", () => {
        expect(paths([loose("a.txt")], addAt("/scene/"))).toEqual(["/scene/a.txt"]);
    });

    it.each([create, addAt("/")])("two folders keep both names and no file is dropped", (ctx) => {
        const plan = planUploadPaths(
            [folder("alpha", ["one.txt", "sub/two.txt"]), folder("beta", ["one.txt"])],
            ctx
        );
        expect(plan.map((p) => p.finalPath).sort()).toEqual([
            "/alpha/one.txt",
            "/alpha/sub/two.txt",
            "/beta/one.txt",
        ]);
        expect(plan.every((p) => p.errors.length === 0)).toBe(true);
    });

    it("a folder with a loose file keeps its name; the file lands at the destination root", () => {
        expect(paths([folder("pack", ["scene.usda"]), loose("readme.txt")], create)).toEqual([
            "/pack/scene.usda",
            "/readme.txt",
        ]);
    });

    it("the override switches a single folder between contents and folder", () => {
        const pack = [folder("pack", ["scene.usda"])];
        expect(paths(pack, { ...create, folderNameMode: "keepName" })).toEqual([
            "/pack/scene.usda",
        ]);
        expect(paths(pack, { ...addAt("/scene/"), folderNameMode: "dropName" })).toEqual([
            "/scene/scene.usda",
        ]);
    });

    it("the override is ignored once the selection is more than one folder", () => {
        expect(
            paths([folder("a", ["x.txt"]), folder("b", ["y.txt"])], {
                ...create,
                folderNameMode: "dropName",
            })
        ).toEqual(["/a/x.txt", "/b/y.txt"]);
    });

    it("duplicate final paths are flagged on every affected row and none is dropped", () => {
        // Two folders of the same name from different places collide on a.txt only.
        const plan = planUploadPaths(
            [folder("pack", ["a.txt"]), folder("pack", ["a.txt", "b.txt"])],
            create
        );
        expect(plan).toHaveLength(3);
        expect(
            plan.filter((p) => p.errors.includes(DUPLICATE_PATH_ERROR)).map((p) => p.id)
        ).toEqual(["0:0", "1:0"]);

        // The same file added twice.
        const same = planUploadPaths([loose("a.txt"), loose("a.txt")], create);
        expect(same).toHaveLength(2);
        expect(same.every((p) => p.errors.includes(DUPLICATE_PATH_ERROR))).toBe(true);
    });

    it("re-evaluates over the whole selection: adding a loose file flips the folder to keep its name", () => {
        const pack = folder("pack", ["scene.usda"]);
        expect(paths([pack], create)).toEqual(["/scene.usda"]);
        expect(paths([pack, loose("notes.txt")], create)).toEqual([
            "/notes.txt",
            "/pack/scene.usda",
        ]);
    });

    it("flags a relative segment inside a folder as an invalid path", () => {
        const plan = planUploadPaths([folder("pack", ["../escape.txt"])], create);
        expect(plan[0].errors).toContain(INVALID_PATH_ERROR);
    });

    it("joins the destination with exactly one slash", () => {
        expect(paths([loose("a.txt")], addAt("scene"))).toEqual(["/scene/a.txt"]);
        expect(paths([loose("a.txt")], addAt("//scene//"))).toEqual(["/scene/a.txt"]);
    });
});

describe("normalizeDestination", () => {
    it.each(["scene", "/scene", "scene/", "//scene//", " /scene/ "])("%s -> /scene/", (input) => {
        expect(normalizeDestination(input)).toEqual({ value: "/scene/" });
    });

    it.each(["", "/", undefined])("%s -> the asset root", (input) => {
        expect(normalizeDestination(input as any)).toEqual({ value: "/" });
    });

    it.each(["../x", "a/./b", "a/../b", "a\\b", "a\u0001b"])("%s is refused", (input) => {
        expect(normalizeDestination(input).error).toBeTruthy();
    });
});

describe("rootsFromInputFiles", () => {
    const withRelative = (name: string, rel: string) => {
        const file = f(name);
        Object.defineProperty(file, "webkitRelativePath", { value: rel });
        return file;
    };

    it("groups a webkitdirectory selection into one folder root", () => {
        const roots = rootsFromInputFiles([
            withRelative("scene.usda", "pack/scene.usda"),
            withRelative("pallet.usda", "pack/props/pallet.usda"),
        ]);
        expect(roots).toHaveLength(1);
        expect(roots[0]).toMatchObject({ kind: "folder", name: "pack" });
        expect((roots[0] as any).files.map((x: any) => x.path)).toEqual([
            "scene.usda",
            "props/pallet.usda",
        ]);
    });

    it("treats plain picked files as loose files", () => {
        const roots = rootsFromInputFiles([f("a.txt"), f("b.txt")]);
        expect(roots.map((r) => r.kind)).toEqual(["file", "file"]);
    });
});

describe("selection helpers", () => {
    it("removeFromRoots drops removed files and empty folders", () => {
        const roots = [folder("a", ["x.txt"]), folder("b", ["y.txt", "z.txt"]), loose("l.txt")];
        const next = removeFromRoots(roots, ["0:0", "1:1", "2:0"]);
        expect(next).toHaveLength(1);
        expect((next[0] as any).files.map((x: any) => x.path)).toEqual(["y.txt"]);
    });

    it("markExisting marks current and archived paths", () => {
        const plan = planUploadPaths(
            [loose("a.png"), loose("b.txt"), loose("c.txt")],
            addAt("/s/")
        );
        const marked = markExisting(
            plan,
            new Map([
                ["/s/a.png", "exists"],
                ["/s/b.txt", "archived"],
            ])
        );
        expect(marked.map((p) => p.existing)).toEqual(["exists", "archived", undefined]);
    });

    it("looseReferencingFiles names loose referencing formats only", () => {
        expect(
            looseReferencingFiles([
                loose("model.GLTF"),
                loose("tileset.json"),
                loose("photo.png"),
                folder("pack", ["scene.usda"]),
            ])
        ).toEqual(["model.GLTF", "tileset.json"]);
    });
});
