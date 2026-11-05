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
 * nothing selected the tree takes 887px, which leaves the details 47px below the handle.
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
// The help text as the panel's width wraps it: two lines, 45px with its padding and top border
const HELP_TEXT_HEIGHT = 45;
// Columns that give the tree the heights it takes in the viewer's panel in 900px, 760px and 640px
// high windows (this layout's 42px header and 16px tree padding take 13px more than the panel's
// 45px header)
const WINDOW_900_COLUMN = 437;
const WINDOW_760_COLUMN = 346;
const WINDOW_640_COLUMN = 268;
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

// Lays the details out too, while nothing is selected: they take the column below the handle, and
// the help text, all they hold then, sits at their top
function layOutWithDetails(this: HTMLElement): DOMRect {
    const handle = document.querySelector<HTMLElement>('[role="separator"]');
    const details = handle?.nextElementSibling;
    if (handle && details && this === details) {
        const top = handle.getBoundingClientRect().bottom;
        return rect(top, Math.max(0, layout.column - top));
    }
    if (details && this.parentElement === details) {
        return rect(details.getBoundingClientRect().top, HELP_TEXT_HEIGHT);
    }
    return layOut.call(this);
}

// The help text lies inside the details' box, which shows nothing outside it
const expectHelpTextShown = () => {
    const help = screen.getByText(/^Click: Select \| Ctrl\+Click: Multi-select/);
    const details = (help.parentElement as HTMLElement).getBoundingClientRect();
    const box = help.getBoundingClientRect();
    expect(box.top).toBeGreaterThanOrEqual(details.top);
    expect(box.bottom).toBeLessThanOrEqual(details.bottom);
};

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

// With nothing selected, in each column: the automatic height, then a height picked with ArrowUp
// in the first column as the column shrinks to the others. Each time the details keep 47px below
// the handle and show the whole help text.
const expectHelpTextRoom = (columns: number[], automaticHeights: number[]) => {
    jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
        layOutWithDetails
    );
    const { resizeColumn } = fakeResizeObserver();
    const expectRoomBelow = (handle: HTMLElement) => {
        expect(detailsHeight(handle)).toBe(47);
        expectHelpTextShown();
    };

    columns.forEach((column, index) => {
        layout.column = column;
        const { unmount } = render(sceneGraph([]));
        expectTreeHeight(getHandle(), automaticHeights[index]);
        expectRoomBelow(getHandle());
        unmount();
    });

    layout.column = columns[0];
    render(sceneGraph([]));
    const handle = getHandle();
    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expectTreeHeight(handle, automaticHeights[0] - 20);
    columns.slice(1).forEach((column, index) => {
        resizeColumn(column);
        expectTreeHeight(handle, automaticHeights[index + 1]);
        expectRoomBelow(handle);
    });
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

        // Nothing selected: the tree leaves the details 47px.
        rerender(sceneGraph([]));
        expectTreeHeight(handle, 887);
        expect(handle).toHaveAttribute("aria-valuemax", "887");
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
        expectTreeHeight(handle, 887);

        fireEvent.keyDown(handle, { key: "End" });
        expectTreeHeight(handle, 887);
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        expectTreeHeight(handle, 867);
        fireEvent.keyDown(handle, { key: "ArrowDown" });
        expectTreeHeight(handle, 867);

        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 734);
        expect(detailsHeight(handle)).toBe(200);
    });

    it("leaves the divider following the selection after keys or a drag that change nothing", () => {
        const { rerender } = render(sceneGraph([]));
        const handle = getHandle();

        fireEvent.keyDown(handle, { key: "End" });
        fireEvent.keyDown(handle, { key: "ArrowDown" });
        expectTreeHeight(handle, 887);
        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 300);

        fireEvent.pointerDown(handle, { pointerId: 1, clientY: 362 });
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 362 });
        fireEvent.pointerUp(handle, { pointerId: 1, clientY: 362 });
        rerender(sceneGraph([]));
        expectTreeHeight(handle, 887);
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

        // Nothing selected: the tree leaves the details 47px, as the column changes size.
        rerender(sceneGraph([]));
        expectTreeHeight(handle, 887);
        resizeColumn(600);
        expectTreeHeight(handle, 487);
        resizeColumn(800);
        expectTreeHeight(handle, 687);

        // Once the user resizes the tree, a taller column leaves the height as it is, a shorter one
        // brings it within the automatic height while nothing is selected, and a selection brings
        // it within the drag's limit.
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        expectTreeHeight(handle, 667);
        resizeColumn(COLUMN_HEIGHT);
        expectTreeHeight(handle, 667);
        resizeColumn(700);
        expectTreeHeight(handle, 587);
        expect(detailsHeight(handle)).toBe(47);
        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 434);
        expect(detailsHeight(handle)).toBe(200);
    });

    it("keeps a tree with nothing selected within its column", () => {
        // In a 330px column the tree takes 217px and leaves the details 47px.
        layout.column = 330;
        const short = render(sceneGraph([]));
        expectTreeHeight(getHandle(), 217);
        expect(detailsHeight(getHandle())).toBe(47);
        short.unmount();

        // A taller header takes its room from the tree: below a 100px header the tree takes 829px.
        layout.column = COLUMN_HEIGHT;
        layout.header = 100;
        const tallHeader = render(sceneGraph([]));
        expectTreeHeight(getHandle(), 829);
        expect(detailsHeight(getHandle())).toBe(47);
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
        expectTreeHeight(handle, 887);
        expect(handle).toHaveAttribute("aria-valuemax", "887");

        // The handle starts at 945px (42px header, 887px tree and its 16px padding); take hold 3px
        // into it.
        fireEvent.pointerDown(handle, { pointerId: 1, pointerType: "mouse", clientY: 948 });
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 950 });
        expectTreeHeight(handle, 887);
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 946 });
        expectTreeHeight(handle, 885);
        expect(946 - handle.getBoundingClientRect().top).toBe(3);
        // Down past the start and back up: the drag stays within the height it started from.
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 1100 });
        expectTreeHeight(handle, 887);
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 761 });
        expectTreeHeight(handle, 700);
        fireEvent.pointerUp(handle, { pointerId: 1, clientY: 761 });

        // The drag picked a height, so the selection keeps it.
        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 700);
    });

    it("keeps a height picked above the drag's limit with nothing selected when the column grows", () => {
        const { resizeColumn } = fakeResizeObserver();
        // As in a 900px high window: the tree takes 324px and a drag allows 171px.
        layout.column = WINDOW_900_COLUMN;
        const keys = render(sceneGraph([]));
        expectTreeHeight(getHandle(), 324);
        fireEvent.keyDown(getHandle(), { key: "ArrowUp" });
        expectTreeHeight(getHandle(), 304);
        resizeColumn(463);
        expectTreeHeight(getHandle(), 304);
        expect(getHandle()).toHaveAttribute("aria-valuemax", "304");
        resizeColumn(COLUMN_HEIGHT);
        expectTreeHeight(getHandle(), 304);
        keys.unmount();

        // The same height picked with a drag. The handle starts at 382px (42px header, 324px tree
        // and its 16px padding); take hold 3px into it and move up 20px.
        layout.column = WINDOW_900_COLUMN;
        render(sceneGraph([]));
        const handle = getHandle();
        fireEvent.pointerDown(handle, { pointerId: 1, pointerType: "mouse", clientY: 385 });
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 365 });
        fireEvent.pointerUp(handle, { pointerId: 1, clientY: 365 });
        expectTreeHeight(handle, 304);
        resizeColumn(463);
        expectTreeHeight(handle, 304);
    });

    it("lowers a height picked with nothing selected only to the automatic height when the column shrinks", () => {
        const { resizeColumn } = fakeResizeObserver();
        render(sceneGraph([]));
        const handle = getHandle();
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        expectTreeHeight(handle, 867);

        // In a 900px column the automatic height is 787px, above the drag's 634px limit: the tree
        // comes down to it, and the details keep the 47px it leaves them.
        resizeColumn(900);
        expectTreeHeight(handle, 787);
        expect(detailsHeight(handle)).toBe(47);

        // In a 350px column the drag's limit stays at its 150px floor; the tree comes down to the
        // automatic height, 237px.
        resizeColumn(350);
        expectTreeHeight(handle, 237);
        expect(detailsHeight(handle)).toBe(47);
        expect(handle).toHaveAttribute("aria-valuemax", "237");

        // A taller column leaves the height as it is; a column too short for the shortest tree
        // leaves it at 150px.
        resizeColumn(COLUMN_HEIGHT);
        expectTreeHeight(handle, 237);
        resizeColumn(200);
        expectTreeHeight(handle, 150);
    });

    it("brings a height picked above the drag's limit within it on a selection, and keeps that when the selection clears", () => {
        const { resizeColumn } = fakeResizeObserver();
        layout.column = WINDOW_900_COLUMN;
        const { rerender } = render(sceneGraph([]));
        const handle = getHandle();
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        resizeColumn(463);
        expectTreeHeight(handle, 304);

        rerender(sceneGraph([mesh]));
        expectTreeHeight(handle, 197);
        expect(detailsHeight(handle)).toBe(200);
        rerender(sceneGraph([]));
        expectTreeHeight(handle, 197);
        resizeColumn(COLUMN_HEIGHT);
        expectTreeHeight(handle, 197);
    });

    it("keeps room for the help text below a height picked with nothing selected when the column shrinks", () => {
        // In 900px, 760px and 640px high windows: 324px, 233px and 155px, and 304px picked.
        expectHelpTextRoom(
            [WINDOW_900_COLUMN, WINDOW_760_COLUMN, WINDOW_640_COLUMN],
            [324, 233, 155]
        );
    });

    it("keeps room for the help text below a handle that is taller to touch, with nothing selected", () => {
        // The 24px handle takes its extra 16px from the tree. In a 640px high window the tree would
        // be shorter than the 150px the keys and a drag allow, so that column is left out.
        layout.handle = 24;
        expectHelpTextRoom([WINDOW_900_COLUMN, WINDOW_760_COLUMN], [324 - 16, 233 - 16]);
    });
});
