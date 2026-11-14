/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A natural-language result's thumbnail is the keyword result's: the same cells, fed by the hit's
 * `str_previewfilekey`, fetch the preview through `downloadAsset` and show what the API answered.
 * A refusal shows the API's own reason (D166), the quarantine sentence included; a preview the API
 * serves, which it does for a quarantined asset (#1495), is shown as the image.
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SearchPageListView from "./SearchPageListView";
import { INITIAL_STATE } from "./SearchPageTypes";
import {
    QUARANTINE_REFUSAL_MESSAGE,
    QUARANTINE_REFUSED_DOWNLOAD,
} from "../../visualizerPlugin/core/quarantineRefusal.testdata";

const mockDownloadAsset = jest.fn();
jest.mock("../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
    fetchAsset: jest.fn(),
    fetchFileInfo: jest.fn(),
    fetchtagTypes: jest.fn(() => Promise.resolve([])),
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

/** A file hit as `POST /search/nlp` returns it (vectorSearchService._file_hit). */
const nlpFileHit = (assetId: string) => ({
    _index: "vams-vector",
    _id: `db-a#${assetId}#/pump.glb#v1`,
    _score: 0.8,
    _index_type: "file",
    _source: {
        str_rectype: "file",
        str_databaseid: "db-a",
        str_assetid: assetId,
        str_key: "/pump.glb",
        str_assetname: "Pump",
        str_fileext: "glb",
        num_filesize: 10,
        str_previewfilekey: "/pump.glb.previewFile.png",
    },
    _vector: { distance: 0.2, segmentHits: 0, bestSegment: null },
});

/** An asset hit as `POST /search/nlp` returns it in asset mode. */
const nlpAssetHit = (assetId: string) => ({
    _index: "vams-vector",
    _id: `db-a#${assetId}`,
    _score: 0.8,
    _index_type: "asset",
    _source: {
        str_rectype: "asset",
        str_databaseid: "db-a",
        str_assetid: assetId,
        str_assetname: "Pump",
        str_previewfilekey: "previews/a1/thumb.png",
    },
});

const renderNlp = (recordType: "file" | "asset", hit: any) =>
    render(
        <MemoryRouter>
            <SearchPageListView
                state={
                    {
                        ...INITIAL_STATE,
                        initialResult: true,
                        recordType,
                        showPreviewThumbnails: true,
                        filters: { ...INITIAL_STATE.filters, _rectype: { value: recordType } },
                        result: { hits: { hits: [hit], total: { value: 1, relation: "eq" } } },
                        tablePreferences: { pageSize: 25, visibleContent: ["preview"] },
                    } as any
                }
                dispatch={jest.fn()}
            />
        </MemoryRouter>
    );

describe("a natural-language result's thumbnail", () => {
    beforeEach(() => {
        mockDownloadAsset.mockReset();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });
    afterEach(() => jest.restoreAllMocks());

    it.each([
        ["file", nlpFileHit("a-refused-file"), "assetFile", "/pump.glb.previewFile.png"],
        ["asset", nlpAssetHit("a-refused-asset"), "assetPreview", "previews/a1/thumb.png"],
    ])("of a %s shows the API's refusal reason", async (recordType, hit, downloadType, key) => {
        mockDownloadAsset.mockResolvedValue(QUARANTINE_REFUSED_DOWNLOAD);
        const { container } = renderNlp(recordType as "file" | "asset", hit);
        expect(await screen.findByText(QUARANTINE_REFUSAL_MESSAGE)).toBeInTheDocument();
        expect(container.querySelector(`[title="${QUARANTINE_REFUSAL_MESSAGE}"]`)).not.toBeNull();
        expect(mockDownloadAsset).toHaveBeenCalledWith(
            expect.objectContaining({ databaseId: "db-a", key, downloadType })
        );
    });

    it.each([
        ["file", nlpFileHit("a-served-file")],
        ["asset", nlpAssetHit("a-served-asset")],
    ])("of a %s shows the preview the API serves", async (recordType, hit) => {
        mockDownloadAsset.mockResolvedValue([true, "data:image/png;base64,AAAA"]);
        const { container } = renderNlp(recordType as "file" | "asset", hit);
        await waitFor(() =>
            expect(container.querySelector('img[src="data:image/png;base64,AAAA"]')).not.toBeNull()
        );
        expect(screen.queryByText(QUARANTINE_REFUSAL_MESSAGE)).not.toBeInTheDocument();
    });
});
