/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Browsing files inside one asset version does not remount DynamicViewer, so the
 * compatibility effect is the only thing that can notice the file changed. It used
 * to touch the selection only when nothing was selected, which left the previously
 * chosen viewer mounted against a file it cannot render, and left a resolved "no
 * compatible viewers" error on screen as the only visible state.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { DynamicViewer } from "./DynamicViewer";

// Built inside the factory (which is hoisted) and read back through require().
jest.mock("../core/PluginRegistry", () => {
    const instance = {
        isInitialized: jest.fn(() => true),
        initialize: jest.fn().mockResolvedValue(undefined),
        getCompatibleViewers: jest.fn(() => [] as any[]),
        switchToPlugin: jest.fn(),
        loadPluginDependencies: jest.fn().mockResolvedValue(undefined),
        getCurrentlyLoadedPlugin: jest.fn(() => null),
        cleanup: jest.fn(),
    };
    return {
        PluginRegistry: { getInstance: () => instance },
        getFileExtensions: (files: any[]) =>
            Array.from(
                new Set(
                    files
                        .filter((file: any) => !file.isDirectory)
                        .map((file: any) =>
                            String(file.filename)
                                .slice(String(file.filename).lastIndexOf("."))
                                .toLowerCase()
                        )
                )
            ),
    };
});

jest.mock("../core/StylesheetManager", () => ({
    StylesheetManager: { getScopedClassName: (id: string) => `scoped-${id}` },
}));

const registry = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../core/PluginRegistry").PluginRegistry.getInstance();

const metadata = (id: string, name: string, extensions: string[]) => ({
    config: {
        id,
        name,
        description: `${name} description`,
        componentPath: `./viewers/${id}`,
        supportedExtensions: extensions,
        supportsMultiFile: false,
        canFullscreen: false,
        priority: 1,
        dependencies: [],
        loadStrategy: "lazy",
        category: "3d",
        enabled: true,
    },
    isLoaded: false,
});

const THREEJS = metadata("threejs-viewer", "Three.js Viewer", [".glb"]);
const IFC = metadata("thatopenwebifc-viewer", "ThatOpen IFC BIM Viewer", [".ifc"]);
const BABYLON = metadata("gaussian-splat-viewer-babylonjs", "BabylonJS Splat", [".glb"]);

const file = (filename: string) => ({
    filename,
    key: `asset1/${filename}`,
    isDirectory: false,
    assetId: "a1",
    databaseId: "d1",
});

const view = (files: any[]) => (
    <DynamicViewer
        files={files as any}
        assetId="a1"
        databaseId="d1"
        viewerMode="wide"
        onViewerModeChange={() => undefined}
    />
);

describe("DynamicViewer", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        registry().isInitialized.mockReturnValue(true);
        registry().getCurrentlyLoadedPlugin.mockReturnValue(null);
        registry().loadPluginDependencies.mockResolvedValue(undefined);
        // Each plugin renders its own id so the mounted viewer is identifiable.
        registry().switchToPlugin.mockImplementation(async (id: string) => ({
            config: [THREEJS, IFC, BABYLON].find((m) => m.config.id === id)!.config,
            component: () => <div>{`viewer:${id}`}</div>,
            isLoaded: true,
        }));
    });

    it("swaps the mounted viewer when the new file needs a different one", async () => {
        registry().getCompatibleViewers.mockReturnValue([THREEJS]);
        const { rerender } = render(view([file("model.glb")]));
        await waitFor(() => expect(screen.getByText(/viewer:threejs-viewer/)).toBeInTheDocument());

        registry().getCompatibleViewers.mockReturnValue([IFC]);
        rerender(view([file("building.ifc")]));

        await waitFor(() =>
            expect(screen.getByText(/viewer:thatopenwebifc-viewer/)).toBeInTheDocument()
        );
        expect(screen.queryByText(/viewer:threejs-viewer/)).toBeNull();
    });

    it("gives the viewer picker a name that says what it does", async () => {
        // Without an ariaLabel the control's only accessible name is its
        // placeholder, and after a choice it is the selected viewer's name.
        //
        // Asserted on the COMPUTED accessible name, not on an `[aria-label="..."]` attribute: this
        // Cloudscape version does not put `ariaLabel` on the control. It renders a hidden span
        // holding the text and points the trigger's `aria-labelledby` at it, so an attribute query
        // finds nothing while the control is in fact correctly named. Resolving `aria-labelledby` is
        // what a screen reader does, so this keeps passing if Cloudscape switches between the two.
        registry().getCompatibleViewers.mockReturnValue([THREEJS, BABYLON]);
        render(view([file("model.glb")]));

        const named = (label: string) =>
            Array.from(document.querySelectorAll("[aria-label],[aria-labelledby]")).some((el) => {
                const direct = el.getAttribute("aria-label") ?? "";
                const resolved = (el.getAttribute("aria-labelledby") ?? "")
                    .split(/\s+/)
                    .filter(Boolean)
                    .map((id) => document.getElementById(id)?.textContent ?? "")
                    .join(" ");
                return `${direct} ${resolved}`.includes(label);
            });

        await waitFor(() => expect(named("Select viewer")).toBe(true));
        // Control: the helper discriminates rather than matching anything.
        expect(named("Select nonexistent control")).toBe(false);
    });

    it("keeps a selection that can still render the new file", async () => {
        // Positive control for the test above: the swap must be driven by the
        // selection falling out of the candidate set, not by every file change.
        registry().getCompatibleViewers.mockReturnValue([THREEJS]);
        const { rerender } = render(view([file("one.glb")]));
        await waitFor(() => expect(screen.getByText(/viewer:threejs-viewer/)).toBeInTheDocument());
        const switchCalls = registry().switchToPlugin.mock.calls.length;

        rerender(view([file("two.glb")]));

        await waitFor(() => expect(screen.getByText(/viewer:threejs-viewer/)).toBeInTheDocument());
        expect(registry().switchToPlugin.mock.calls.length).toBe(switchCalls);
    });

    it("clears a no-compatible-viewer error once the user moves to a viewable file", async () => {
        registry().getCompatibleViewers.mockReturnValue([]);
        const { rerender } = render(view([file("data.zzz")]));
        await waitFor(() =>
            expect(
                screen.getByText(/No compatible viewers found for file types: \.zzz/)
            ).toBeInTheDocument()
        );

        // Two candidates: the selection stays null, so the load effect — the only
        // other place that cleared the error — never runs.
        registry().getCompatibleViewers.mockReturnValue([THREEJS, BABYLON]);
        rerender(view([file("model.glb")]));

        await waitFor(() => expect(screen.queryByText(/No compatible viewers found/)).toBeNull());
    });
});
