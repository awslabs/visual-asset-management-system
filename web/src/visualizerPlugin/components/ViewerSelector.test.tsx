/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A compare-only viewer (the text differ) renders nothing but `compareFiles`, which the visualize path
 * never passes. The registry already keeps it out of the visualize candidates; the selector applies the
 * same rule to whatever list it is handed so the Visualize dropdown can never list a differ — for one
 * file, for N files, or from a caller that built its own list.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { ViewerSelector, listableViewers } from "./ViewerSelector";
import type { ViewerPlugin } from "../core/PluginRegistry";

// The registry module pulls in Vite's import.meta.glob, which jest cannot parse; the selector only
// needs the pure predicate (imported from core/viewerSelection), so the registry is stubbed out.
jest.mock("../core/PluginRegistry", () => ({}));

const plugin = (id: string, name: string, overrides: Record<string, any> = {}): ViewerPlugin =>
    ({
        config: {
            id,
            name,
            description: `${name} description`,
            componentPath: `./viewers/${id}`,
            supportedExtensions: [".txt"],
            supportsMultiFile: false,
            canFullscreen: true,
            priority: 1,
            dependencies: [],
            loadStrategy: "lazy",
            category: "document",
            ...overrides,
        },
        component: null as any,
        isLoaded: false,
    } as ViewerPlugin);

const textViewer = plugin("text-viewer", "Text Viewer");
const differ = plugin("text-diff-viewer", "Text Diff Viewer", {
    compareMode: {
        enabled: true,
        compareOnly: true,
        minFiles: 2,
        maxFiles: 2,
        allowSameFileDifferentVersions: true,
        allowDifferentFiles: true,
    },
});
const hybrid = plugin("dual", "Dual Viewer", {
    compareMode: {
        enabled: true,
        minFiles: 2,
        maxFiles: 2,
        allowSameFileDifferentVersions: true,
        allowDifferentFiles: true,
    },
});

describe("listableViewers", () => {
    it("drops compare-only viewers in visualize mode and keeps everything else", () => {
        expect(
            listableViewers([textViewer, differ, hybrid], "visualize").map((v) => v.config.id)
        ).toEqual(["text-viewer", "dual"]);
    });

    it("lists the handed-in viewers untouched in compare mode", () => {
        expect(listableViewers([differ, hybrid], "compare").map((v) => v.config.id)).toEqual([
            "text-diff-viewer",
            "dual",
        ]);
    });
});

describe("ViewerSelector", () => {
    it("never renders a compare-only viewer in the Visualize dropdown", () => {
        render(
            <ViewerSelector
                viewers={[textViewer, differ]}
                selectedViewerId={null}
                onViewerChange={() => undefined}
                mode="visualize"
            />
        );
        // One listable viewer → the control is not flagged as "selection required".
        expect(screen.queryByText(/required/i)).toBeNull();
        expect(screen.queryByText("Text Diff Viewer")).toBeNull();
    });

    it("renders nothing when only compare-only viewers were handed in for Visualize", () => {
        const { container } = render(
            <ViewerSelector
                viewers={[differ]}
                selectedViewerId={null}
                onViewerChange={() => undefined}
                mode="visualize"
            />
        );
        expect(container).toBeEmptyDOMElement();
    });

    it("still offers the differ in compare mode", () => {
        render(
            <ViewerSelector
                viewers={[differ]}
                selectedViewerId="text-diff-viewer"
                onViewerChange={() => undefined}
                mode="compare"
            />
        );
        expect(screen.getByText("Text Diff Viewer")).toBeInTheDocument();
    });
});
