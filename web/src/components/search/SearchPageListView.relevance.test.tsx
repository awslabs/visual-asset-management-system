/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { MemoryRouter } from "react-router-dom";
import SearchPageListView from "./SearchPageListView";
import type { NlpVectorInfo } from "./types";

// The viewer registry uses import.meta.glob, which Jest cannot parse; the list view only asks
// whether a row's extension is viewable, so a registry that knows no viewer is enough.
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({
    PluginRegistry: {
        getInstance: () => ({
            isInitialized: () => true,
            initialize: jest.fn().mockResolvedValue(undefined),
            getCompatibleViewers: () => [],
        }),
    },
    getFileExtension: (filename: string) => filename.slice(filename.lastIndexOf(".")).toLowerCase(),
    getFileExtensions: () => [],
}));
jest.mock("../../services/APIService", () => ({
    fetchtagTypes: jest.fn().mockResolvedValue([]),
    fetchAsset: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
    searchAssets: jest.fn(),
}));
jest.mock("./SearchResults/MapThumbnail", () => ({ __esModule: true, default: () => null }));
jest.mock("../filemanager/modals/FileViewerModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../filemanager/modals/AssetPreviewModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../modals/AssetDeleteModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../modals/AssetUnarchiveModal", () => ({ __esModule: true, default: () => null }));

// A hit matched by its whole-file vector only, and one matched through a content chunk.
const wholeFileVector: NlpVectorInfo = {
    distance: 0.126,
    embeddingModelId: "m",
    sourceModalities: ["render", "text"],
    indexedAt: "2026-09-08T00:00:00Z",
    fileClass: "mesh",
    segmentHits: 0,
    bestSegment: null,
};
const chunkVector: NlpVectorInfo = {
    ...wholeFileVector,
    fileClass: "document",
    segmentHits: 2,
    bestSegment: {
        segmentKey: "c000004",
        segmentKind: "textChunk",
        segmentLabel: "chunk 5/12 · page 5",
        segmentStartMs: null,
        segmentEndMs: null,
    },
};

const state = (visibleContent: string[], vector: NlpVectorInfo = wholeFileVector) => ({
    initialResult: true,
    loading: false,
    recordType: "file",
    filters: { _rectype: { label: "Files", value: "file" } },
    tablePreferences: { pageSize: 50, visibleContent },
    pagination: { from: 0, size: 50 },
    sort: [],
    tableSort: {},
    selectedItems: [],
    columnNames: [],
    viewerSelectMode: false,
    viewerSelection: [],
    showPreviewThumbnails: false,
    showMapThumbnails: false,
    useMapView: false,
    setViewerSelection: jest.fn(),
    enterViewerSelectMode: jest.fn(),
    exitViewerSelectMode: jest.fn(),
    clearViewerSelection: jest.fn(),
    result: {
        hits: {
            total: { value: 1, relation: "eq" },
            hits: [
                {
                    _id: "db1#a1#/models/truck.glb#v1",
                    _score: 0.874,
                    _vector: vector,
                    _source: {
                        str_databaseid: "db1",
                        str_assetid: "a1",
                        str_key: "/models/truck.glb",
                        str_fileext: ".glb",
                    },
                },
            ],
        },
    },
});

// The table header carries its own info popover, so the relevance cell's popover is the one
// rendered inside the table body.
const relevancePopover = (container: HTMLElement) => {
    const table = createWrapper(container).findTable()!;
    const popover = table.findBodyCell(1, 2)!.findPopover();
    expect(popover).not.toBeNull();
    return popover!;
};

describe("SearchPageListView relevance column", () => {
    it("renders the score as a percentage under a Relevance header", () => {
        render(
            <MemoryRouter>
                <SearchPageListView state={state(["str_key", "relevance"])} dispatch={jest.fn()} />
            </MemoryRouter>
        );
        // Cloudscape renders the column header twice (the sticky-header copy is aria-hidden).
        expect(screen.getAllByText("Relevance").length).toBeGreaterThan(0);
        expect(screen.getByText("87%")).toBeInTheDocument();
    });

    it("renders nothing for relevance when the column is not visible", () => {
        render(
            <MemoryRouter>
                <SearchPageListView state={state(["str_key"])} dispatch={jest.fn()} />
            </MemoryRouter>
        );
        expect(screen.queryByText("Relevance")).toBeNull();
        expect(screen.queryByText("87%")).toBeNull();
    });

    it("names the best chunk and the number of matching chunks in the popover", () => {
        const { container } = render(
            <MemoryRouter>
                <SearchPageListView
                    state={state(["str_key", "relevance"], chunkVector)}
                    dispatch={jest.fn()}
                />
            </MemoryRouter>
        );
        const popover = relevancePopover(container);
        popover.findTrigger().click();
        const content = popover.findContent()!.getElement().textContent ?? "";
        expect(content).toContain("render, text");
        expect(content).toContain("chunk 5/12 · page 5 (2 matching chunks)");
    });

    it("shows only the modalities when no segment vector matched", () => {
        const { container } = render(
            <MemoryRouter>
                <SearchPageListView state={state(["str_key", "relevance"])} dispatch={jest.fn()} />
            </MemoryRouter>
        );
        const popover = relevancePopover(container);
        popover.findTrigger().click();
        const content = popover.findContent()!.getElement().textContent ?? "";
        expect(content).toContain("render, text");
        expect(content).not.toContain("matching");
    });

    it("opens the popover from a focusable, labelled trigger", () => {
        const { container } = render(
            <MemoryRouter>
                <SearchPageListView state={state(["str_key", "relevance"])} dispatch={jest.fn()} />
            </MemoryRouter>
        );
        const trigger = relevancePopover(container)
            .findTrigger()
            .getElement()
            .querySelector("button");
        expect(trigger).not.toBeNull();
        expect(trigger).toHaveAttribute("aria-label", "Show what this result matched from");
        expect(trigger).not.toHaveAttribute("tabindex", "-1");
    });
});
