/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The handle between the scene tree and the details below it: its grip icon, its separator role and
 * values, and resizing by keyboard, mouse and touch. The column the tree, handle and details share
 * is COLUMN_HEIGHT tall here, so the tree stays between 150px and 800px (the details keep 200px).
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import SceneGraph from "./SceneGraph";
import { GRIP_ICON_SVG } from "../../../components/GripIconSvg";

const COLUMN_HEIGHT = 1000;

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
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
            top: 0,
            height: COLUMN_HEIGHT,
        } as DOMRect);
    });

    afterEach(() => {
        jest.restoreAllMocks();
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
        expect(handle).toHaveAttribute("aria-valuemax", "800");
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
        expectTreeHeight(handle, 800);
        fireEvent.keyDown(handle, { key: "ArrowDown" });
        expectTreeHeight(handle, 800);
    });

    it("resizes the tree with a mouse drag, capturing the pointer", () => {
        render(sceneGraph([mesh]));
        const handle = getHandle();
        handle.setPointerCapture = jest.fn();

        fireEvent.pointerDown(handle, { pointerId: 1, pointerType: "mouse", clientY: 300 });
        expect(handle.setPointerCapture).toHaveBeenCalledWith(1);
        expect(document.body.style.cursor).toBe("ns-resize");
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 420 });
        expectTreeHeight(handle, 420);
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 950 });
        expectTreeHeight(handle, 800);
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 20 });
        expectTreeHeight(handle, 150);
        fireEvent.pointerUp(handle, { pointerId: 1, clientY: 20 });
        expect(document.body.style.cursor).toBe("");

        // Moves after the drag ends do nothing.
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 500 });
        expectTreeHeight(handle, 150);
    });

    it("resizes the tree with a touch drag", () => {
        render(sceneGraph([mesh]));
        const handle = getHandle();
        expect(handle.style.touchAction).toBe("none");

        fireEvent.pointerDown(handle, { pointerId: 2, pointerType: "touch", clientY: 300 });
        fireEvent.pointerMove(handle, { pointerId: 2, pointerType: "touch", clientY: 600 });
        expectTreeHeight(handle, 600);
        fireEvent.pointerCancel(handle, { pointerId: 2, pointerType: "touch" });
        fireEvent.pointerMove(handle, { pointerId: 2, pointerType: "touch", clientY: 700 });
        expectTreeHeight(handle, 600);
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

        fireEvent.pointerDown(handle, { pointerId: 1, clientY: 300 });
        fireEvent.pointerMove(handle, { pointerId: 1, clientY: 420 });
        fireEvent.pointerUp(handle, { pointerId: 1, clientY: 420 });
        expectTreeHeight(handle, 420);

        rerender(sceneGraph([mesh, group]));
        expectTreeHeight(handle, 420);
        rerender(sceneGraph([]));
        expectTreeHeight(handle, 420);

        fireEvent.keyDown(handle, { key: "ArrowDown" });
        rerender(sceneGraph([group]));
        expectTreeHeight(handle, 440);
    });
});
