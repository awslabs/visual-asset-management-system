/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shared upload control: Add files, Add folder and the drop zone all produce the same roots,
 * so a picked folder and a dropped folder land at the same path (#1462, #1471).
 *
 * The drop mocks reproduce two browser behaviours: a DataTransferItemList is only readable while
 * the drop event is dispatched (afterwards its length is 0 and getAsFile returns null), and
 * FileSystemFileEntry.file / FileSystemDirectoryReader.readEntries call back asynchronously, with
 * a directory listing delivered in several batches that ends with an empty one.
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DragDropFileUpload from "./DragDropFileUpload";
import { UploadRoot, planUploadPaths } from "./uploadPaths";

function makeFile(name: string): File {
    return new File(["x"], name, { type: "text/plain" });
}

function fileEntry(name: string) {
    const file = makeFile(name);
    return {
        isFile: true,
        isDirectory: false,
        name,
        file: (resolve: (f: File) => void) => setTimeout(() => resolve(file), 5),
    };
}

function dirEntry(name: string, batches: any[][]) {
    return {
        isFile: false,
        isDirectory: true,
        name,
        createReader: () => {
            const pending = [...batches, []];
            return {
                readEntries: (resolve: (entries: any[]) => void) =>
                    setTimeout(() => resolve(pending.shift() ?? []), 1),
            };
        },
    };
}

/**
 * An item list that stops being readable once the drop handler yields. An `entry` of undefined
 * models a browser without webkitGetAsEntry.
 */
function liveItemList(items: { entry?: any; file: File | null }[]) {
    let live = true;
    Promise.resolve().then(() => {
        live = false;
    });
    const list: any = {};
    Object.defineProperty(list, "length", { get: () => (live ? items.length : 0) });
    items.forEach(({ entry, file }, index) => {
        const item: any = {
            kind: "file",
            getAsFile: () => (live ? file : null),
        };
        if (entry !== undefined) {
            item.webkitGetAsEntry = () => (live ? entry : null);
        }
        Object.defineProperty(list, index, { get: () => (live ? item : undefined) });
    });
    return list;
}

// The same tree, pack/{scene.usda, props/pallet.usda}, in each of the three browser shapes.
const packEntry = () =>
    dirEntry("pack", [[fileEntry("scene.usda"), dirEntry("props", [[fileEntry("pallet.usda")]])]]);

const fileHandle = (name: string) => ({ kind: "file", name, getFile: async () => makeFile(name) });
const dirHandle = (name: string, children: any[]) => ({
    kind: "directory",
    name,
    values: async function* () {
        for (const c of children) yield c;
    },
});
const packHandle = () =>
    dirHandle("pack", [fileHandle("scene.usda"), dirHandle("props", [fileHandle("pallet.usda")])]);

const packInputFiles = () =>
    [
        ["scene.usda", "pack/scene.usda"],
        ["pallet.usda", "pack/props/pallet.usda"],
    ].map(([name, rel]) => {
        const file = makeFile(name);
        Object.defineProperty(file, "webkitRelativePath", { value: rel });
        return file;
    });

function renderZone() {
    const onAdd = jest.fn();
    render(<DragDropFileUpload label="Files" onAdd={onAdd} />);
    const zone = screen.getByTestId("upload-drop-zone");
    return { onAdd, zone };
}

const rootShape = (roots: UploadRoot[]) =>
    roots.map((r) =>
        r.kind === "file"
            ? { kind: r.kind, name: r.name }
            : { kind: r.kind, name: r.name, paths: r.files.map((x) => x.path).sort() }
    );

const createAssetPaths = (roots: UploadRoot[]) =>
    planUploadPaths(roots, { flow: "createAsset", destination: "/", folderNameMode: "auto" })
        .map((p) => p.finalPath)
        .sort();

const addFilesPaths = (roots: UploadRoot[]) =>
    planUploadPaths(roots, { flow: "addFiles", destination: "/scene/", folderNameMode: "auto" })
        .map((p) => p.finalPath)
        .sort();

async function dropPack() {
    const { onAdd, zone } = renderZone();
    fireEvent.drop(zone, {
        dataTransfer: { items: liveItemList([{ entry: packEntry(), file: null }]) },
    });
    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
    return onAdd.mock.calls[0][0] as UploadRoot[];
}

describe("DragDropFileUpload drop handling", () => {
    afterEach(() => {
        delete (window as any).showDirectoryPicker;
    });

    it("collects every file from two dropped folders and a loose file", async () => {
        const { onAdd, zone } = renderZone();
        const loose = makeFile("loose.txt");
        const items = liveItemList([
            {
                entry: dirEntry("alpha", [
                    [fileEntry("one.txt"), dirEntry("sub", [[fileEntry("two.txt")]])],
                    [fileEntry("three.txt")],
                ]),
                file: null,
            },
            { entry: dirEntry("beta", [[fileEntry("four.txt")]]), file: null },
            { entry: { isFile: true, isDirectory: false, name: "loose.txt" }, file: loose },
        ]);

        fireEvent.drop(zone, { dataTransfer: { items } });

        await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
        const roots: UploadRoot[] = onAdd.mock.calls[0][0];
        expect(rootShape(roots)).toEqual([
            { kind: "folder", name: "alpha", paths: ["one.txt", "sub/two.txt", "three.txt"] },
            { kind: "folder", name: "beta", paths: ["four.txt"] },
            { kind: "file", name: "loose.txt" },
        ]);
        expect((roots[2] as any).handle).toBe(loose);
        // Several roots: every folder keeps its name.
        expect(createAssetPaths(roots)).toEqual([
            "/alpha/one.txt",
            "/alpha/sub/two.txt",
            "/alpha/three.txt",
            "/beta/four.txt",
            "/loose.txt",
        ]);
    });

    it("uses the File for an item when the browser provides no entry API", async () => {
        const { onAdd, zone } = renderZone();
        const file = makeFile("plain.bin");

        fireEvent.drop(zone, { dataTransfer: { items: liveItemList([{ file }]) } });

        await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
        expect(onAdd.mock.calls[0][0]).toEqual([{ kind: "file", name: "plain.bin", handle: file }]);
    });

    it("#1471: a single dropped folder on Create Asset becomes the asset's contents", async () => {
        const roots = await dropPack();
        expect(createAssetPaths(roots)).toEqual(["/props/pallet.usda", "/scene.usda"]);
    });

    it("#1471: Add folder through showDirectoryPicker and a drop of the same folder give identical paths", async () => {
        const dropped = await dropPack();

        (window as any).showDirectoryPicker = jest.fn(async () => packHandle());
        const onAdd = jest.fn();
        render(<DragDropFileUpload label="Picker" onAdd={onAdd} />);
        fireEvent.click(screen.getAllByRole("button", { name: "Add folder" })[1]);
        await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
        const picked: UploadRoot[] = onAdd.mock.calls[0][0];

        expect(rootShape(picked)).toEqual(rootShape(dropped));
        expect(createAssetPaths(picked)).toEqual(createAssetPaths(dropped));
        expect(addFilesPaths(picked)).toEqual(addFilesPaths(dropped));
        expect(addFilesPaths(picked)).toEqual([
            "/scene/pack/props/pallet.usda",
            "/scene/pack/scene.usda",
        ]);
    });

    it("#1471: without showDirectoryPicker, Add folder uses the webkitdirectory input with the same paths", async () => {
        const dropped = await dropPack();

        const onAdd = jest.fn();
        render(<DragDropFileUpload label="Input" onAdd={onAdd} />);
        const inputs = screen.getAllByTestId("upload-folder-input");
        const input = inputs[inputs.length - 1] as HTMLInputElement;
        const clickSpy = jest.spyOn(input, "click");
        fireEvent.click(screen.getAllByRole("button", { name: "Add folder" })[1]);
        expect(clickSpy).toHaveBeenCalled();
        expect(screen.queryByText(/Folder selection is not supported/)).toBeNull();

        fireEvent.change(input, { target: { files: packInputFiles() } });
        expect(onAdd).toHaveBeenCalledTimes(1);
        const picked: UploadRoot[] = onAdd.mock.calls[0][0];
        expect(rootShape(picked)).toEqual(rootShape(dropped));
        expect(createAssetPaths(picked)).toEqual(["/props/pallet.usda", "/scene.usda"]);
    });

    it("falls back to the folder input when showDirectoryPicker fails for a reason other than cancel", async () => {
        (window as any).showDirectoryPicker = jest.fn(async () => {
            throw new Error("SecurityError");
        });
        const { onAdd } = renderZone();
        const input = screen.getByTestId("upload-folder-input") as HTMLInputElement;
        const clickSpy = jest.spyOn(input, "click");
        fireEvent.click(screen.getByRole("button", { name: "Add folder" }));
        await waitFor(() => expect(clickSpy).toHaveBeenCalled());
        expect(onAdd).not.toHaveBeenCalled();
    });

    it("exposes labelled Add files and Add folder buttons and a directory input", () => {
        renderZone();
        expect(screen.getByRole("button", { name: "Add files" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Add folder" })).toBeTruthy();
        expect(screen.getByRole("group", { name: /drop files or folders/i })).toBeTruthy();
        const folderInput = screen.getByTestId("upload-folder-input");
        expect(folderInput.hasAttribute("webkitdirectory")).toBe(true);
        const filesInput = screen.getByTestId("upload-files-input") as HTMLInputElement;
        expect(filesInput.multiple).toBe(true);
        expect(filesInput.hasAttribute("webkitdirectory")).toBe(false);
    });

    it("Add files delivers loose files from the plain input", () => {
        const { onAdd } = renderZone();
        const input = screen.getByTestId("upload-files-input");
        fireEvent.change(input, { target: { files: [makeFile("a.txt"), makeFile("b.txt")] } });
        expect(rootShape(onAdd.mock.calls[0][0])).toEqual([
            { kind: "file", name: "a.txt" },
            { kind: "file", name: "b.txt" },
        ]);
    });
});
