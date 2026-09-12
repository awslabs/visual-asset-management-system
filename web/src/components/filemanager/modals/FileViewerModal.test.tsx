/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Visualize/Compare toggle offers a mode only when at least one registered viewer admits the
 * selection on that path. Two text files have a differ but no multi-file visualizer, so the modal
 * opens straight on Compare with no toggle; one text file is the reverse; a selection neither path
 * can open shows an empty state instead of an inner "No compatible viewers" error. Before this, the
 * toggle always showed both modes and the unavailable one dead-ended inside the viewer.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import FileViewerModal from "./FileViewerModal";

const mockAvailableModes = jest.fn();
jest.mock("../../../visualizerPlugin/core/viewableExtensions", () => ({
    availableModesForFiles: (files: any[]) => mockAvailableModes(files),
    extensionOfFilename: (name?: string) => {
        if (!name) return undefined;
        const dot = name.lastIndexOf(".");
        return dot <= 0 ? undefined : name.slice(dot);
    },
}));
jest.mock("../../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
// The registry pulls in Vite's import.meta.glob; only the ViewerMode type is used here.
jest.mock("../../../visualizerPlugin/core/PluginRegistry", () => ({}));
// Stand in for the viewer: it prints the mode it was rendered in.
jest.mock("../../../visualizerPlugin/components/DynamicViewer", () => ({
    DynamicViewer: ({ mode }: any) => <div data-testid="dynamic-viewer">mode:{mode}</div>,
}));

const file = (filename: string) => ({
    filename,
    key: `/${filename}`,
    isDirectory: false,
    assetId: "asset-1",
    databaseId: "db-1",
});

const renderModal = (files: any[], initialMode?: "visualize" | "compare") =>
    render(
        <FileViewerModal
            visible
            onDismiss={() => undefined}
            files={files}
            databaseId="db-1"
            assetId="asset-1"
            initialMode={initialMode}
        />
    );

// SegmentedControl renders its label twice (the segment group and a narrow-viewport fallback Select),
// so presence is "at least one" and absence is "none".
const modeToggle = () => screen.queryAllByLabelText("Viewer mode")[0] ?? null;

describe("FileViewerModal mode availability", () => {
    beforeEach(() => mockAvailableModes.mockReset());

    it("shows the toggle only when both modes are admitted", () => {
        mockAvailableModes.mockReturnValue({ visualize: true, compare: true });
        renderModal([file("a.txt"), file("b.txt")]);
        expect(modeToggle()).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Visualize" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Compare" })).toBeInTheDocument();
        expect(screen.getByTestId("dynamic-viewer").textContent).toBe("mode:visualize");
    });

    it("forces Compare, without a toggle, when only a compare viewer admits the selection", () => {
        mockAvailableModes.mockReturnValue({ visualize: false, compare: true });
        renderModal([file("a.txt"), file("b.txt")], "visualize");
        expect(modeToggle()).toBeNull();
        expect(screen.getByTestId("dynamic-viewer").textContent).toBe("mode:compare");
        expect(screen.getByText(/Compare Files - 2 Files/)).toBeInTheDocument();
    });

    it("forces Visualize, without a toggle, when only a visualize viewer admits the selection", () => {
        mockAvailableModes.mockReturnValue({ visualize: true, compare: false });
        renderModal([file("a.txt")], "compare");
        expect(modeToggle()).toBeNull();
        expect(screen.getByTestId("dynamic-viewer").textContent).toBe("mode:visualize");
        expect(screen.getByText(/File Viewer - a\.txt/)).toBeInTheDocument();
    });

    it("shows a clear empty state when neither mode has a viewer", () => {
        mockAvailableModes.mockReturnValue({ visualize: false, compare: false });
        renderModal([file("a.png"), file("b.bin")]);
        expect(modeToggle()).toBeNull();
        expect(screen.queryByTestId("dynamic-viewer")).toBeNull();
        expect(screen.getByTestId("file-viewer-no-viewer")).toBeInTheDocument();
        expect(screen.getByText("No viewer for this selection")).toBeInTheDocument();
        expect(screen.getByText(/2 files of type \.png, \.bin/)).toBeInTheDocument();
    });

    it("asks about the selection once with the files it was given", () => {
        mockAvailableModes.mockReturnValue({ visualize: true, compare: true });
        const files = [file("a.txt"), file("b.txt")];
        renderModal(files);
        expect(mockAvailableModes).toHaveBeenCalledTimes(1);
        expect(mockAvailableModes).toHaveBeenCalledWith(files);
    });
});
