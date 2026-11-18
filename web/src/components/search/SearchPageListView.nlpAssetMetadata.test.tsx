/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The table's Metadata column for a natural-language asset row (D83). The row's MD_ holds its asset's
 * location keys only, carried for the Map view, so the column lists no metadata for it; a keyword
 * row's MD_ is its record's metadata and keeps its list.
 */
import React from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import SearchPageListView from "./SearchPageListView";
import { INITIAL_STATE } from "./SearchPageTypes";

jest.mock("../../services/APIService", () => ({
    fetchtagTypes: jest.fn(() => Promise.resolve([])),
}));
jest.mock("./SearchPreviewThumbnail/PreviewThumbnailCell", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../filemanager/modals/FileViewerModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../../visualizerPlugin/core/viewableExtensions", () => ({
    areFilenamesViewableTogether: () => false,
    areFilesComparableTogether: () => false,
}));
jest.mock("../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({}));

const assetSource = (MD_: Record<string, any>) => ({
    str_rectype: "asset",
    str_databaseid: "db-a",
    str_assetid: "a1",
    str_assetname: "Pump house",
    MD_,
});

const renderRows = (hit: any) =>
    render(
        <MemoryRouter>
            <SearchPageListView
                state={
                    {
                        ...INITIAL_STATE,
                        initialResult: true,
                        recordType: "asset",
                        result: { hits: { hits: [hit], total: { value: 1, relation: "eq" } } },
                        tablePreferences: { pageSize: 25, visibleContent: ["metadata"] },
                    } as any
                }
                dispatch={jest.fn()}
            />
        </MemoryRouter>
    );

/** The popover in the one row's cells: the Metadata column is the only column with one. */
const metadataCellPopover = (container: HTMLElement) => {
    const table = createWrapper(container).findTable();
    if (!table) throw new Error("the list view renders no table");
    const rows = table.findRows();
    expect(rows).toHaveLength(1);
    return createWrapper(rows[0].getElement()).findPopover();
};

describe("Metadata column for a natural-language asset row (D83)", () => {
    beforeEach(() => jest.spyOn(console, "log").mockImplementation(() => undefined));
    afterEach(() => jest.restoreAllMocks());

    it("lists no metadata for a natural-language asset row whose MD_ holds its location keys", () => {
        const { container } = renderRows({
            _id: "db-a#a1",
            _score: 0.8,
            _index_type: "asset",
            _source: {
                ...assetSource({ Latitude: "38.9", Longitude: "-77.0" }),
                geo_MD_location: { type: "Point", coordinates: [-77.0, 38.9] },
            },
            _vector: { distance: 0.2, sourceModalities: [], segmentHits: 0, bestSegment: null },
        });
        expect(metadataCellPopover(container)).toBeNull();
        expect(container).not.toHaveTextContent("Metadata Fields");
    });

    it("keeps the metadata list of a keyword asset row", () => {
        const { container } = renderRows({
            _id: "db-a#a1",
            _source: assetSource({ Latitude: "38.9", Longitude: "-77.0", site: "Plant-7" }),
        });
        expect(metadataCellPopover(container)).not.toBeNull();
    });
});
