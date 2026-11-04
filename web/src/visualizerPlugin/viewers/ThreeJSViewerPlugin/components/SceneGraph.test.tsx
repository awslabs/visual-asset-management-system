/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The handle between the scene tree and the details below it: its grip icon, its separator role and
 * values, and resizing by keyboard, mouse and touch. The column here lays out the header (Show All /
 * Hide All), the tree (its style height plus its 8px padding above and below), the handle and the
 * details from the top down: with a 1000px column, a 42px header and an 8px handle, the tree's
 * style height stays between 150px and 734px, so the details below the handle keep 200px. With
 * nothing selected the tree takes the column's height less 100px, 900px.
 */

import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import SceneGraph from "./SceneGraph";
import { GRIP_ICON_SVG } from "../../../components/GripIconSvg";

const originalResizeObserver = window.ResizeObserver;

const COLUMN_HEIGHT = 1000;
const HEADER_HEIGHT = 42;
const HANDLE_HEIGHT = 8;
// The tree's padding above and below its style height
const TREE_PADDING = 16;
const layout = { column: COLUMN_HEIGHT, header: HEADER_HEIGHT, handle: HANDLE_HEIGHT };

type FakeObject = {
    uuid: string;
    name: string;
    type: string;
    visible: boolean;
    children: FakeObject[];
    userData: Record<string, unknown>;
    traverse: (visit: (obj: FakeObject) => void) => void;
};

const makeObject = (
    uuid: string,
    name: string,
    type: string,
    children: FakeObject[] = []
): FakeObject => {
    const obj: FakeObject = {
        uuid,
        name,
        type,
        visible: true,
        children,
        userData: {},
        traverse: (visit) => {
            visit(obj);
            obj.children.forEach((child) => child.traverse(visit));
        },
    };
    return obj;
};

const mesh = makeObject("mesh-0001", "Cube", "Mesh");
const group = makeObject("group-0001", "Parts", "Group");
const roots = [makeObject("file-0001", "model.glb", "Group", [mesh, group])];

const sceneGraph = (selectedObjects: FakeObject[]) => (
    <SceneGraph
        scene={{}}
        camera={{}}
        threeRoot={roots}
        controls={{}}
        selectedObjects={selectedObjects}
        onSelectObjects={jest.fn()}
        materialLibrary={new Map()}
        onAssignMaterial={jest.fn()}
        onMakeUnique={jest.fn()}
        onCreateAndAssign={jest.fn()}
        onEditMaterial={jest.fn()}
    />
);

const getHandle = () => screen.getByRole("separator", { name: "Resize tree view" });

const getTree = (handle: HTMLElement) =>
    document.getElementById(handle.getAttribute("aria-controls") ?? "") as HTMLElement;

const expectTreeHeight = (handle: HTMLElement, height: number) => {
    expect(getTree(handle).style.height).toBe(`${height}px`);
    expect(handle).toHaveAttribute("aria-valuenow", String(height));
};

// The handle reads only the top, bottom and height of a box
const rect = (top: number, height: number) => ({ top, height, bottom: top + height } as DOMRect);

// Lays the column out from the top down: the header, the tree with its padding, the handle
function layOut(this: HTMLElement): DOMRect {
    const handle = document.querySelector<HTMLElement>('[role="separator"]');
    const tree = handle?.previousElementSibling as HTMLElement | null | undefined;
    const treeBox = parseFloat(tree?.style.height ?? "0") + TREE_PADDING;
    if (handle && this === handle.parentElement) return rect(0, layout.column);
    if (tree && this === tree) return rect(layout.header, treeBox);
    if (handle && this === handle) return rect(layout.header + treeBox, layout.handle);
    return rect(0, 0);
}

// Room the details get below the handle
const detailsHeight = (handle: HTMLElement) =>
    layout.column - handle.getBoundingClientRect().bottom;

// Stands in for ResizeObserver, which jsdom does not implement; resizeColumn calls back the
// connected observers, as a browser does when the column changes size
const fakeResizeObserver = () => {
    const observers: { callback: () => void; target?: Element; disconnected: boolean }[] = [];
    (window as any).ResizeObserver = class {
        entry: (typeof observers)[number];
        constructor(callback: () => void) {
            this.entry = { callback, disconnected: false };
            observers.push(this.entry);
        }
        observe(target: Element) {
            this.entry.target = target;
        }
        disconnect() {
            this.entry.disconnected = true;
        }
    };
    const resizeColumn = (height: number) => {
        layout.column = height;
        act(() => observers.filter((o) => !o.disconnected).forEach((o) => o.callback()));
    };
    return { observers, resizeColumn };
};

describe("SceneGraph tree resize handle", () => {
    const hadPointerEvent = "PointerEvent" in window;

    beforeAll(() => {
        // Selecting an object scrolls its row into view, which jsdom does not implement.
        Element.prototype.scrollIntoView = jest.fn();
        // jsdom has no PointerEvent, so fireEvent would drop clientY; a MouseEvent subclass carries
        // it, as a browser's PointerEvent does.
        if (!hadPointerEvent) {
            (window as any).PointerEvent = class extends MouseEvent {
                pointerId: number;
                pointerType: string;
                constructor(type: string, init: any = {}) {
                    super(type, init);
                    this.pointerId = init.pointerId ?? 0;
                    this.pointerType = init.pointerType ?? "mouse";
                }
            };
        }
    });

    afterAll(() => {
        if (!hadPointerEvent) delete (window as any).PointerEvent;
    });

    beforeEach(() => {
        Object.assign(layout, {
            column: COLUMN_HEIGHT,
            header: HEADER_HEIGHT,
            handle: HANDLE_HEIGHT,
        });
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(layOut);
    });

    afterEach(() => {
        jest.restoreAllMocks();
        window.ResizeObserver = originalResizeObserver;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
    });

    it("draws the grip as the grip icon and keeps the tooltip", () => {
        const gripMarkup = render(<>{GRIP_ICON_SVG}</>, {
            container: document.createElement("div"),
        }).container.innerHTML;
        render(sceneGraph([mesh]));

        const handle = screen.getByTitle("Drag to resize tree view");
        expect(handle.textContent).not.toContain("\u22EE");
        const icons = Array.from(handle.querySelectorAll("svg"));
        expect(icons).toHaveLength(1);
        expect(icons[0]).toHaveAttribute("aria-hidden", "true");
        expect(icons[0].outerHTML).toBe(gripMarkup);
    });

    it("is a focusable horizontal separator that names the tree it sizes", () => {
        render(sceneGraph([mesh]));
        const handle = getHandle();

        expect(handle).toHaveAttribute("aria-orientation", "horizontal");
        expect(handle).toHaveAttribute("tabindex", "0");
        expect(handle).toHaveAttribute("title", "Drag to resize tree view");
        const tree = getTree(handle);
        expect(tree).toBe(handle.previousElementSibling);
        expect(tree).toContainElement(document.querySelector('[data-node-id="mesh-0001"]'));
        expectTreeHeight(handle, 300);
        expect(handle).toHaveAttribute("aria-valuetext", "300 pixels");
        expect(handle).toHaveAttribute("aria-valuemin", "150");
        expect(handle).toHaveAttribute("aria-valuemax", "734");
    });

    it("moves the divider with the arrow keys, Home and End within the drag's limits", () => {
        render(sceneGraph([mesh]));
        const handle = getHandle();
        handle.focus();
        expect(handle).toHaveFocus();

        fireEvent.keyDown(handle, { key: "ArrowDown" });
        expectTreeHeight(handle, 320);
        expect(handle).toHaveAttribute("aria-valuetext", "320 pixels");
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        expectTreeHeight(handle, 280);
        fireEvent.keyDown(handle, { key: "a" });
        expectTreeHeight(handle, 280);

        fireEvent.keyDown(handle, { key: "Home" });
        expectTreeHeight(handle, 150);
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        expectTreeHeight(handle, 150);

        fireEvent.keyDown(handle, { key: "End" });
        expectTreeHeight(handle, 734);
        expect(detailsHeight(handle)).toBe(200);
        fireEvent.keyDown(handle, { key: "ArrowDown" });
        expectTreeHeight(handle, 734);
    });

    it("keeps the handle under the pointer during a mouse drag, measured from the tree's top", () => {
        render(sceneGraph([mesh]));
        const handle = getHandle();
        handle.setPointerCapture = jest.fn();
        const pointerIntoHandle = (clientY: number) => clientY - handle.getBoundingClientRect().top;

        // The handle starts at 358px (42px header, 300px tree and its 16px padding); take hold 4px into it.
        fireEvent.pointerDown(handle, { pointerId: 1, pointerType: "mouse", clientY: 362 });
        expect(handle.setPointerCapture).toHaveBeenCalledWith(1);
        expect(document.body.style.cursor).toBe("ns-resize");
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 482 });
        expectTreeHeight(handle, 420);
        expect(pointerIntoHandle(482)).toBe(4);
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 1100 });
        expectTreeHeight(handle, 734);
        expect(detailsHeight(handle)).toBe(200);
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 20 });
        expectTreeHeight(handle, 150);
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 600 });
        expectTreeHeight(handle, 538);
        expect(pointerIntoHandle(600)).toBe(4);
        fireEvent.pointerUp(handle, { pointerId: 1, clientY: 600 });
        expect(document.body.style.cursor).toBe("");

        // Moves after the drag ends do nothing.
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 500 });
        expectTreeHeight(handle, 538);
    });

    it("starts a drag only with the primary mouse button", () => {
        render(sceneGraph([mesh]));
        const handle = getHandle();
        handle.setPointerCapture = jest.fn();

        // A right-button press keeps its default (the context menu) and does not drag.
        const notPrevented = fireEvent.pointerDown(handle, {
            pointerId: 1,
            pointerType: "mouse",
            button: 2,
            clientY: 362,
        });
        expect(notPrevented).toBe(true);
        expect(handle.setPointerCapture).not.toHaveBeenCalled();
        expect(document.body.style.cursor).toBe("");
        fireEvent.pointerMove(handle, { pointerId: 1, pointerType: "mouse", clientY: 500 });
        expectTreeHeight(handle, 300);
    });

    it("ends the drag when the handle loses the pointer capture", () => {
        render(sceneGraph([mesh]));
        const handle = getHandle();

        fireEvent.pointerDown(handle, { pointerId: 1, pointerType: "mouse", clientY: 362 });
        expect(document.body.style.cursor).toBe("ns-resize");
        fireEvent.lostPointerCapture(handle, { pointerId: 1 });
        expect(document.body.style.cursor).toBe("");
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 482 });
        expectTreeHeight(handle, 300);
    });

    it("resizes the tree with a touch drag", () => {
        render(sceneGraph([mesh]));
        const handle = getHandle();
        expect(handle.style.touchAction).toBe("none");

        fireEvent.pointerDown(handle, { pointerId: 2, pointerType: "touch", clientY: 362 });
        fireEvent.pointerMove(handle, { pointerId: 2, pointerType: "touch", clientY: 662 });
        expectTreeHeight(handle, 600);
        fireEvent.pointerCancel(handle, { pointerId: 2, pointerType: "touch" });
        fireEvent.pointerMove(handle, { pointerId: 2, pointerType: "touch", clientY: 700 });
        expectTreeHeight(handle, 600);
    });

    it("keeps 200px for the details below a handle that is taller to touch", () => {
        layout.handle = 24;
        render(sceneGraph([mesh]));
        const handle = getHandle();
        expect(handle).toHaveAttribute("aria-valuemax", "718");

        // Take hold 2px into the 24px handle, above the bar it draws in its middle.
        fireEvent.pointerDown(handle, { pointerId: 2, pointerType: "touch", clientY: 360 });
        fireEvent.pointerMove(handle, { pointerId: 2, pointerType: "touch", clientY: 480 });
        expectTreeHeight(handle, 420);
        expect(480 - handle.getBoundingClientRect().top).toBe(2);
        fireEvent.pointerUp(handle, { pointerId: 2, pointerType: "touch", clientY: 480 });

        fireEvent.keyDown(handle, { key: "End" });
        expectTreeHeight(handle, 718);
        expect(detailsHeight(handle)).toBe(200);
    });

    it("moves the divider with the selection until the user resizes the tree, then keeps that height", () => {
        const { rerender } = render(sceneGraph([mesh]));
        const handle = getHandle();
        expectTreeHeight(handle, 300);

        // Nothing selected: the tree takes the column's height less 100px.
        rerender(sceneGraph([]));
        expectTreeHeight(handle, 900);
        expect(handle).toHaveAttribute("aria-valuemax", "900");
        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 300);

        fireEvent.pointerDown(handle, { pointerId: 1, clientY: 362 });
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 482 });
        fireEvent.pointerUp(handle, { pointerId: 1, clientY: 482 });
        expectTreeHeight(handle, 420);

        rerender(sceneGraph([mesh, group]));
        expectTreeHeight(handle, 420);
        rerender(sceneGraph([]));
        expectTreeHeight(handle, 420);

        fireEvent.keyDown(handle, { key: "ArrowDown" });
        rerender(sceneGraph([group]));
        expectTreeHeight(handle, 440);
    });

    it("brings a height picked with the keys while nothing is selected within the drag's limit on the next selection", () => {
        const { rerender } = render(sceneGraph([]));
        const handle = getHandle();
        expectTreeHeight(handle, 900);

        fireEvent.keyDown(handle, { key: "End" });
        expectTreeHeight(handle, 900);
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        expectTreeHeight(handle, 880);
        fireEvent.keyDown(handle, { key: "ArrowDown" });
        expectTreeHeight(handle, 880);

        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 734);
        expect(detailsHeight(handle)).toBe(200);
    });

    it("leaves the divider following the selection after keys or a drag that change nothing", () => {
        const { rerender } = render(sceneGraph([]));
        const handle = getHandle();

        fireEvent.keyDown(handle, { key: "End" });
        fireEvent.keyDown(handle, { key: "ArrowDown" });
        expectTreeHeight(handle, 900);
        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 300);

        fireEvent.pointerDown(handle, { pointerId: 1, clientY: 362 });
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 362 });
        fireEvent.pointerUp(handle, { pointerId: 1, clientY: 362 });
        rerender(sceneGraph([]));
        expectTreeHeight(handle, 900);
    });

    it("keeps a height the user picked within the drag's limit when the header above the tree grows", () => {
        const { rerender } = render(sceneGraph([mesh]));
        const handle = getHandle();
        // 21 ArrowDown presses take the tree from 300px to 720px.
        for (let press = 0; press < 21; press++) {
            fireEvent.keyDown(handle, { key: "ArrowDown" });
        }
        expectTreeHeight(handle, 720);

        // A header 34px taller leaves less room below the tree; the selection stays the same.
        layout.header = HEADER_HEIGHT + 34;
        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 700);
        expect(handle).toHaveAttribute("aria-valuemax", "700");
        expect(detailsHeight(handle)).toBe(200);
    });

    it("keeps a height the user picked within the drag's limit when the column shrinks", () => {
        const { observers, resizeColumn } = fakeResizeObserver();
        const { unmount } = render(sceneGraph([mesh]));
        const handle = getHandle();
        const column = handle.parentElement;
        expect(observers.some((o) => o.target === column)).toBe(true);

        fireEvent.keyDown(handle, { key: "ArrowDown" });
        expectTreeHeight(handle, 320);
        resizeColumn(500);
        expectTreeHeight(handle, 234);
        expect(handle).toHaveAttribute("aria-valuemax", "234");
        expect(detailsHeight(handle)).toBe(200);

        // A taller column leaves the picked height as it is.
        resizeColumn(COLUMN_HEIGHT);
        expectTreeHeight(handle, 234);
        expect(handle).toHaveAttribute("aria-valuemax", "734");

        unmount();
        expect(observers.every((o) => o.disconnected)).toBe(true);
    });

    it("caps the tree's automatic height after a selection at the drag's limit", () => {
        // A 600px column has room for the 300px tree and 234px of details below it.
        layout.column = 600;
        const roomy = render(sceneGraph([mesh]));
        expectTreeHeight(getHandle(), 300);
        expect(detailsHeight(getHandle())).toBe(234);
        roomy.unmount();

        // In a 450px column the drag allows a 184px tree, which leaves the details 200px.
        layout.column = 450;
        const short = render(sceneGraph([]));
        short.rerender(sceneGraph([mesh]));
        expectTreeHeight(getHandle(), 184);
        expect(getHandle()).toHaveAttribute("aria-valuemax", "184");
        expect(detailsHeight(getHandle())).toBe(200);
        short.unmount();

        // A 380px column is too short for 200px below the shortest tree: the tree takes 150px, as
        // End does there.
        layout.column = 380;
        render(sceneGraph([mesh]));
        const handle = getHandle();
        expectTreeHeight(handle, 150);
        expect(detailsHeight(handle)).toBe(164);
        fireEvent.keyDown(handle, { key: "End" });
        expectTreeHeight(handle, 150);
    });

    it("follows the column with the automatic height until the user resizes the tree", () => {
        const { resizeColumn } = fakeResizeObserver();
        const { rerender } = render(sceneGraph([mesh]));
        const handle = getHandle();
        expectTreeHeight(handle, 300);
        resizeColumn(450);
        expectTreeHeight(handle, 184);
        expect(detailsHeight(handle)).toBe(200);
        resizeColumn(COLUMN_HEIGHT);
        expectTreeHeight(handle, 300);

        // Nothing selected: the column's height less 100px, as the column changes size.
        rerender(sceneGraph([]));
        expectTreeHeight(handle, 900);
        resizeColumn(600);
        expectTreeHeight(handle, 500);
        resizeColumn(800);
        expectTreeHeight(handle, 700);

        // Once the user resizes the tree, a taller column leaves the height as it is, a shorter one
        // brings it within the drag's limit, and the selection does not move it.
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        expectTreeHeight(handle, 680);
        resizeColumn(COLUMN_HEIGHT);
        expectTreeHeight(handle, 680);
        resizeColumn(700);
        expectTreeHeight(handle, 434);
        expect(detailsHeight(handle)).toBe(200);
        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 434);
    });

    it("keeps a tree with nothing selected within its column", () => {
        // In a 330px column the column's height less 100px fits.
        layout.column = 330;
        const short = render(sceneGraph([]));
        expectTreeHeight(getHandle(), 230);
        expect(getHandle().getBoundingClientRect().bottom).toBeLessThanOrEqual(330);
        short.unmount();

        // Below a 100px header the tree takes 876px, so the handle ends at the column's bottom.
        layout.column = COLUMN_HEIGHT;
        layout.header = 100;
        const tallHeader = render(sceneGraph([]));
        expectTreeHeight(getHandle(), 876);
        expect(getHandle().getBoundingClientRect().bottom).toBe(COLUMN_HEIGHT);
        tallHeader.unmount();

        // Never shorter than the shortest tree the keys and a drag allow.
        layout.header = HEADER_HEIGHT;
        layout.column = 200;
        render(sceneGraph([]));
        expectTreeHeight(getHandle(), 150);
    });

    it("starts a drag from a tree taller than the drag allows without a jump", () => {
        const { rerender } = render(sceneGraph([]));
        const handle = getHandle();
        expectTreeHeight(handle, 900);
        expect(handle).toHaveAttribute("aria-valuemax", "900");

        // The handle starts at 958px (42px header, 900px tree and its 16px padding); take hold 3px
        // into it.
        fireEvent.pointerDown(handle, { pointerId: 1, pointerType: "mouse", clientY: 961 });
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 963 });
        expectTreeHeight(handle, 900);
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 959 });
        expectTreeHeight(handle, 898);
        expect(959 - handle.getBoundingClientRect().top).toBe(3);
        // Down past the start and back up: the drag stays within the height it started from.
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 1100 });
        expectTreeHeight(handle, 900);
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 761 });
        expectTreeHeight(handle, 700);
        fireEvent.pointerUp(handle, { pointerId: 1, clientY: 761 });

        // The drag picked a height, so the selection keeps it.
        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 700);
    });
});
