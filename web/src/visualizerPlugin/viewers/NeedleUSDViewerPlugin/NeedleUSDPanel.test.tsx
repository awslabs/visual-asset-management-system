/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The scene tree height the user picks in the Needle USD panel stays while the panel switches to
 * another tab and back, and while the panel is hidden and shown, for as long as the viewer shows the
 * same file. A small host stands in for NeedleUSDViewerComponent, whose USD module cannot run here:
 * like the viewer, it holds the height with useKeptSceneTreeHeight and shows or hides the real panel
 * and scene graph; the other two tabs are stand-ins. The scene graph's column is laid out from the
 * top down as in NeedleUSDSceneGraph.test.tsx: in a 1000px column with a 42px header and an 8px
 * handle, a drag or End allows a tree of 734px, and the tree takes 887px while nothing is selected.
 */

import React, { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import NeedleUSDPanel from "./NeedleUSDPanel";
import { useKeptSceneTreeHeight } from "../../components/useKeptSceneTreeHeight";

jest.mock("./NeedleUSDMaterialLibrary", () => () => "Material library tab");
jest.mock("./NeedleUSDControls", () => () => "Controls tab");

const COLUMN_HEIGHT = 1000;
const HEADER_HEIGHT = 42;
const HANDLE_HEIGHT = 8;
// The tree's padding above and below its style height
const TREE_PADDING = 16;
const layout = { column: COLUMN_HEIGHT, header: HEADER_HEIGHT };

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
const roots = [makeObject("file-0001", "model.usdz", "Group", [mesh])];

interface HostProps {
    fileKey: string;
    selectedObjects: FakeObject[];
}

// Holds the kept height and shows or hides the panel, as NeedleUSDViewerComponent does
const ViewerHost: React.FC<HostProps> = ({ fileKey, selectedObjects }) => {
    const keptTreeHeight = useKeptSceneTreeHeight(fileKey);
    const [showPanel, setShowPanel] = useState(true);
    if (!showPanel) {
        return (
            <button title="Show controls panel" onClick={() => setShowPanel(true)}>
                Panel
            </button>
        );
    }
    return (
        <NeedleUSDPanel
            scene={{}}
            camera={{}}
            renderer={{}}
            usdRoot={roots}
            controls={{}}
            selectedObjects={selectedObjects}
            onSelectObjects={jest.fn()}
            onClose={() => setShowPanel(false)}
            materialLibrary={new Map()}
            selectedMaterialId={null}
            onSelectMaterial={jest.fn()}
            onCreateMaterial={jest.fn()}
            onRenameMaterial={jest.fn()}
            onMaterialChange={jest.fn()}
            onResetMaterial={jest.fn()}
            onDuplicateMaterial={jest.fn()}
            onDeleteMaterial={jest.fn()}
            onAssignMaterial={jest.fn()}
            onMakeUnique={jest.fn()}
            onCreateAndAssign={jest.fn()}
            onEditMaterial={jest.fn()}
            onResetAllTransforms={jest.fn()}
            onResetAllMaterials={jest.fn()}
            onClearSelection={jest.fn()}
            enable3DSelection={true}
            onToggle3DSelection={jest.fn()}
            animationPaused={true}
            onToggleAnimation={jest.fn()}
            keptTreeHeight={keptTreeHeight}
        />
    );
};

const host = (selectedObjects: FakeObject[] = [], fileKey = "models/model.usdz") => (
    <ViewerHost fileKey={fileKey} selectedObjects={selectedObjects} />
);

const getHandle = () => screen.getByRole("separator", { name: "Resize tree view" });

const getTree = (handle: HTMLElement) =>
    document.getElementById(handle.getAttribute("aria-controls") ?? "") as HTMLElement;

const expectTreeHeight = (handle: HTMLElement, height: number) => {
    expect(getTree(handle).style.height).toBe(`${height}px`);
    expect(handle).toHaveAttribute("aria-valuenow", String(height));
};

// Home, then three ArrowDown presses: a 210px tree
const pickHeight = (handle: HTMLElement) => {
    fireEvent.keyDown(handle, { key: "Home" });
    for (let press = 0; press < 3; press++) {
        fireEvent.keyDown(handle, { key: "ArrowDown" });
    }
    expectTreeHeight(handle, 210);
};

const switchTabAway = () => {
    fireEvent.click(screen.getByTitle("Material Library"));
    expect(screen.queryByRole("separator")).toBeNull();
    expect(screen.getByText("Material library tab")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("Controls"));
    expect(screen.getByText("Controls tab")).toBeInTheDocument();
};

const switchTabBack = () => fireEvent.click(screen.getByTitle("Scene Graph"));

// Room the details get below the handle
const detailsHeight = (handle: HTMLElement) =>
    layout.column - handle.getBoundingClientRect().bottom;

// The handle reads only the top, bottom and height of a box
const rect = (top: number, height: number) => ({ top, height, bottom: top + height } as DOMRect);

// Lays the scene graph's column out from the top down: the header, the tree with its padding, the
// handle
function layOut(this: HTMLElement): DOMRect {
    const handle = document.querySelector<HTMLElement>('[role="separator"]');
    const tree = handle?.previousElementSibling as HTMLElement | null | undefined;
    const treeBox = parseFloat(tree?.style.height ?? "0") + TREE_PADDING;
    if (handle && this === handle.parentElement) return rect(0, layout.column);
    if (tree && this === tree) return rect(layout.header, treeBox);
    if (handle && this === handle) return rect(layout.header + treeBox, HANDLE_HEIGHT);
    return rect(0, 0);
}

describe("NeedleUSDPanel scene tree height", () => {
    beforeAll(() => {
        Element.prototype.scrollIntoView = jest.fn();
    });

    beforeEach(() => {
        Object.assign(layout, { column: COLUMN_HEIGHT, header: HEADER_HEIGHT });
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(layOut);
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("keeps a height the user picked when the panel switches to another tab and back, through a selection change made meanwhile", () => {
        const { rerender } = render(host());
        expectTreeHeight(getHandle(), 887);

        // Not resized: the tree follows a selection made while it was away.
        switchTabAway();
        rerender(host([mesh]));
        switchTabBack();
        expectTreeHeight(getHandle(), 300);

        pickHeight(getHandle());
        switchTabAway();
        rerender(host([]));
        switchTabBack();
        expectTreeHeight(getHandle(), 210);

        // The kept height is the latest one the user picked.
        fireEvent.keyDown(getHandle(), { key: "ArrowDown" });
        switchTabAway();
        switchTabBack();
        expectTreeHeight(getHandle(), 230);
    });

    it("keeps a height the user picked when the panel is hidden and shown again", () => {
        render(host([mesh]));
        pickHeight(getHandle());

        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));
        expect(screen.queryByRole("separator")).toBeNull();
        fireEvent.click(screen.getByTitle("Show controls panel"));
        expectTreeHeight(getHandle(), 210);
    });

    it("brings a kept height within the drag's limit when the column or the header changed while the tree was away", () => {
        render(host([mesh]));
        fireEvent.keyDown(getHandle(), { key: "End" });
        fireEvent.keyDown(getHandle(), { key: "ArrowUp" });
        expectTreeHeight(getHandle(), 714);

        switchTabAway();
        layout.column = 700;
        switchTabBack();
        expectTreeHeight(getHandle(), 434);
        expect(detailsHeight(getHandle())).toBe(200);

        layout.column = COLUMN_HEIGHT;
        fireEvent.keyDown(getHandle(), { key: "End" });
        expectTreeHeight(getHandle(), 734);
        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));
        layout.header = HEADER_HEIGHT + 34;
        fireEvent.click(screen.getByTitle("Show controls panel"));
        expectTreeHeight(getHandle(), 700);
        expect(detailsHeight(getHandle())).toBe(200);
    });

    it("keeps a height picked above the drag's limit with nothing selected when the panel switches tabs or is hidden", () => {
        const { rerender } = render(host());
        fireEvent.keyDown(getHandle(), { key: "ArrowUp" });
        expectTreeHeight(getHandle(), 867);

        switchTabAway();
        switchTabBack();
        expectTreeHeight(getHandle(), 867);
        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));
        fireEvent.click(screen.getByTitle("Show controls panel"));
        expectTreeHeight(getHandle(), 867);

        // A selection brings it within the drag's limit.
        rerender(host([mesh]));
        expectTreeHeight(getHandle(), 734);
        expect(detailsHeight(getHandle())).toBe(200);
    });

    it("starts a new file at the default height", () => {
        const { rerender } = render(host([mesh]));
        pickHeight(getHandle());
        switchTabAway();
        switchTabBack();
        expectTreeHeight(getHandle(), 210);

        // The viewer is given another file while the tree is away.
        switchTabAway();
        rerender(host([], "models/other.usdz"));
        switchTabBack();
        expectTreeHeight(getHandle(), 887);
    });

    it("starts a viewer that is opened again at the default height", () => {
        const { unmount } = render(host([mesh]));
        pickHeight(getHandle());
        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));
        fireEvent.click(screen.getByTitle("Show controls panel"));
        expectTreeHeight(getHandle(), 210);
        unmount();

        render(host([mesh]));
        expectTreeHeight(getHandle(), 300);
    });
});
