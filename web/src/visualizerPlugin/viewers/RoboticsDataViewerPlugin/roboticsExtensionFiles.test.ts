/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The robotics viewer renders Lichtblick with no extension loader, so a .foxe extension can never
 * be installed. Upstream still accepts a dropped .foxe file and reports "No extension loader found
 * for namespace local". The overlay's refuseExtensionFiles takes such a file away from the app's
 * handlers first and reports that extensions are not available. These tests dispatch real events
 * through the same phases the app's listeners use: a bubble-phase drop listener on the document,
 * and a change listener on an ancestor of the file input (where React listens).
 */

import {
    EXTENSION_FILE_REFUSED,
    EXTENSIONS_UNAVAILABLE,
    isExtensionFileName,
    refuseExtensionFiles,
} from "../../../../customInstalls/robotics/overlay/vamsExtensionFiles";

const file = (name: string): File => new File(["x"], name);

/** A cancelable drop event carrying `files`, as the browser dispatches it on a drop. */
function dropEvent(files: File[]): Event {
    const event = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "dataTransfer", { value: { files } });
    return event;
}

/** A file input inside a container that listens for change, as React's root does. */
function fileInput(files: File[]): { input: HTMLInputElement; root: HTMLDivElement } {
    const root = document.createElement("div");
    const input = document.createElement("input");
    input.type = "file";
    Object.defineProperty(input, "files", { value: files });
    root.appendChild(input);
    document.body.appendChild(root);
    return { input, root };
}

describe("refusing Lichtblick extension files", () => {
    let notified: string[];
    let appDrops: Event[];
    let dragLeaves: number;
    let remove: () => void;
    const onAppDrop = (event: Event) => appDrops.push(event);
    const onDragLeave = () => {
        dragLeaves += 1;
    };

    beforeEach(() => {
        notified = [];
        appDrops = [];
        dragLeaves = 0;
        document.addEventListener("drop", onAppDrop);
        document.addEventListener("dragleave", onDragLeave);
        remove = refuseExtensionFiles(window, (message) => notified.push(message));
    });

    afterEach(() => {
        remove();
        document.removeEventListener("drop", onAppDrop);
        document.removeEventListener("dragleave", onDragLeave);
        document.body.innerHTML = "";
    });

    it("names .foxe files the way upstream's file handling does", () => {
        expect(isExtensionFileName("probe.foxe")).toBe(true);
        expect(isExtensionFileName("layout.json")).toBe(false);
        expect(isExtensionFileName("probe.foxe.json")).toBe(false);
    });

    it("cancels a drop carrying a .foxe file before the app sees it, and says why", () => {
        const event = dropEvent([file("probe.foxe")]);
        document.body.dispatchEvent(event);

        expect(appDrops).toHaveLength(0);
        expect(event.defaultPrevented).toBe(true);
        expect(notified).toEqual([EXTENSION_FILE_REFUSED]);
        // The app's "Drop a file here" overlay closes on dragleave.
        expect(dragLeaves).toBe(1);
    });

    it("refuses the whole drop when a .foxe file comes with a layout file", () => {
        document.body.dispatchEvent(dropEvent([file("layout.json"), file("probe.foxe")]));

        expect(appDrops).toHaveLength(0);
        expect(notified).toEqual([EXTENSION_FILE_REFUSED]);
    });

    it("[control] leaves a drop without a .foxe file to the app", () => {
        const event = dropEvent([file("layout.json")]);
        document.body.dispatchEvent(event);

        expect(appDrops).toEqual([event]);
        expect(event.defaultPrevented).toBe(false);
        expect(notified).toEqual([]);
        expect(dragLeaves).toBe(0);
    });

    it("cancels a .foxe chosen in a file input before the app's change handler, and clears it", () => {
        const { input, root } = fileInput([file("probe.foxe")]);
        const appChanges: Event[] = [];
        root.addEventListener("change", (event) => appChanges.push(event));
        const clear = jest.spyOn(input, "value", "set");

        input.dispatchEvent(new Event("change", { bubbles: true }));

        expect(appChanges).toHaveLength(0);
        expect(notified).toEqual([EXTENSION_FILE_REFUSED]);
        expect(clear).toHaveBeenCalledWith("");
    });

    it("[control] leaves another file input change to the app", () => {
        const { input, root } = fileInput([file("layout.json")]);
        const appChanges: Event[] = [];
        root.addEventListener("change", (event) => appChanges.push(event));

        input.dispatchEvent(new Event("change", { bubbles: true }));

        expect(appChanges).toHaveLength(1);
        expect(notified).toEqual([]);
    });

    it("stops listening once removed", () => {
        remove();
        const event = dropEvent([file("probe.foxe")]);
        document.body.dispatchEvent(event);

        expect(appDrops).toEqual([event]);
        expect(notified).toEqual([]);
        remove = () => undefined;
    });

    it("says the same thing as the Extensions settings notice", () => {
        expect(EXTENSION_FILE_REFUSED.startsWith(EXTENSIONS_UNAVAILABLE.replace(/\.$/, ""))).toBe(
            true
        );
        expect(EXTENSION_FILE_REFUSED).not.toMatch(/loader|namespace/i);
    });
});
