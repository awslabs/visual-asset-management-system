/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Dropping folders and files onto the upload zone collects every file.
 *
 * The mocks reproduce two browser behaviours: a DataTransferItemList is only readable while the
 * drop event is dispatched (afterwards its length is 0 and getAsFile returns null), and
 * FileSystemFileEntry.file / FileSystemDirectoryReader.readEntries call back asynchronously, with
 * a directory listing delivered in several batches that ends with an empty one.
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DragDropFileUpload from "./DragDropFileUpload";

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

function renderZone() {
    const onSelect = jest.fn();
    render(
        <DragDropFileUpload
            label="Files"
            onSelect={onSelect}
            multiFile={true}
            selectionMode="both"
        />
    );
    const zone = screen.getByText("Drag and drop files here").closest("div[style]") as HTMLElement;
    return { onSelect, zone };
}

describe("DragDropFileUpload drop handling", () => {
    it("collects every file from two dropped folders and a loose file", async () => {
        const { onSelect, zone } = renderZone();
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

        await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1));
        const [directoryHandle, fileHandles] = onSelect.mock.calls[0];
        expect(directoryHandle).toEqual({ name: "alpha" });
        expect(fileHandles.map((h: any) => h.path).sort()).toEqual([
            "alpha/one.txt",
            "alpha/sub/two.txt",
            "alpha/three.txt",
            "beta/four.txt",
            "loose.txt",
        ]);
        expect(fileHandles.find((h: any) => h.path === "loose.txt").handle).toBe(loose);
        expect(fileHandles.every((h: any) => h.handle instanceof File)).toBe(true);
    });

    it("uses the File for an item when the browser provides no entry API", async () => {
        const { onSelect, zone } = renderZone();
        const file = makeFile("plain.bin");
        const items = liveItemList([{ file }]);

        fireEvent.drop(zone, { dataTransfer: { items } });

        await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1));
        expect(onSelect.mock.calls[0][1]).toEqual([{ handle: file, path: "plain.bin" }]);
    });
});
