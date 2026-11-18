/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A natural-language result opens its own asset whatever its id holds (#1476, #1486).
 *
 * An asset id may carry a space, a comma or an apostrophe, and may end in '#', '%' or a character
 * such as '／' (the backend's filename pattern). `POST /search/nlp` hits render through the same list
 * and map views as keyword hits, which encode the id with `encodeURIComponent`, so each link a
 * natural-language row or popup offers is read back by the router as the hit's own asset id, and a
 * file row's link keeps its file path in `filePath`.
 */
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { matchRoutes } from "react-router";
import SearchPageListView from "./SearchPageListView";
import SearchPageMapView from "./SearchPageMapView";
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
    isViewableFilename: () => false,
}));
jest.mock("../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({}));
jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        default: ReactActual.forwardRef((props: any, ref: any) => {
            ReactActual.useImperativeHandle(ref, () => ({ fitBounds: () => undefined }));
            return <div data-testid="map">{props.children}</div>;
        }),
        Marker: (props: any) => (
            <button
                type="button"
                data-testid="marker"
                onClick={() =>
                    props.onClick({ originalEvent: { stopPropagation: () => undefined } })
                }
            />
        ),
        Popup: (props: any) => <div data-testid="popup">{props.children}</div>,
        NavigationControl: () => null,
        Source: () => null,
        Layer: () => null,
    };
});
jest.mock("../../common/utils/maplibreWorker", () => ({}));
jest.mock("../../services/appCache", () => ({
    appCache: { getItem: () => ({ locationServiceApiUrl: "https://maps.example.com/style" }) },
}));

const ASSET_IDS = ["pump house 2", "pump-q#", "pump 50%", "o'neil, rev b", "cut／"];
const FILE_PATH = "/scans/part #1 (50%).glb";
const ROUTES = [{ path: "/databases/:databaseId/assets/:assetId" }];

const vector = {
    distance: 0.2,
    embeddingModelId: "m",
    sourceModalities: ["genai-metadata"],
    indexedAt: "2026-11-10T00:00:00Z",
    fileClass: "model",
    segmentHits: 0,
    bestSegment: null,
};

/** A natural-language hit as `POST /search/nlp` returns it (vectorSearchService._file_hit / _asset_hits). */
const nlpHit = (rectype: "asset" | "file", assetId: string, extra: Record<string, any> = {}) => ({
    _index: "vector",
    _id: rectype === "asset" ? `db-a#${assetId}` : `db-a#${assetId}#${FILE_PATH}#v1`,
    _score: 0.8,
    _index_type: rectype,
    _source: {
        str_rectype: rectype,
        str_databaseid: "db-a",
        str_assetid: assetId,
        str_assetname: `Asset ${assetId}`,
        ...(rectype === "file" ? { str_key: FILE_PATH, str_fileext: "glb", num_filesize: 10 } : {}),
        bool_archived: false,
        ...extra,
    },
    _vector: vector,
});

/** The asset id and file path the router reads from an `#/databases/...` href. */
const target = (href: string | null) => {
    expect(href).toMatch(/^#\/databases\//);
    const [pathPart, query] = href!.slice(1).split("?");
    const match = matchRoutes(ROUTES, pathPart);
    return {
        databaseId: match?.[0].params.databaseId,
        assetId: match?.[0].params.assetId,
        filePath: query ? new URLSearchParams(query).get("filePath") : null,
    };
};

const assetLinks = (container: HTMLElement) =>
    Array.from(container.querySelectorAll("a[href]"))
        .map((a) => a.getAttribute("href"))
        .filter((href): href is string => !!href && /\/assets\/[^/]/.test(href));

const renderList = (rectype: "asset" | "file", hits: any[], visibleContent: string[]) =>
    render(
        <MemoryRouter>
            <SearchPageListView
                state={
                    {
                        ...INITIAL_STATE,
                        initialResult: true,
                        recordType: rectype,
                        filters: { ...INITIAL_STATE.filters, _rectype: { value: rectype } },
                        result: { hits: { hits, total: { value: hits.length, relation: "gte" } } },
                        tablePreferences: { pageSize: 25, visibleContent },
                    } as any
                }
                dispatch={jest.fn()}
            />
        </MemoryRouter>
    );

describe("a natural-language result opens its own asset", () => {
    beforeEach(() => jest.spyOn(console, "log").mockImplementation(() => undefined));
    afterEach(() => jest.restoreAllMocks());

    it.each(ASSET_IDS)("from an asset row of the list view (%s)", (assetId) => {
        const { container } = renderList("asset", [nlpHit("asset", assetId)], ["str_assetname"]);
        const links = assetLinks(container);
        expect(links).toHaveLength(1);
        expect(target(links[0])).toEqual({ databaseId: "db-a", assetId, filePath: null });
    });

    it.each(ASSET_IDS)("from a file row of the list view, with its file path (%s)", (assetId) => {
        const { container } = renderList("file", [nlpHit("file", assetId)], ["str_key"]);
        const links = assetLinks(container);
        expect(links).toHaveLength(1);
        expect(target(links[0])).toEqual({ databaseId: "db-a", assetId, filePath: FILE_PATH });
    });

    it.each(ASSET_IDS)("from the Map view's popup of an asset hit (%s)", (assetId) => {
        const hit = nlpHit("asset", assetId, { MD_: { lat: "38.9", lon: "-77.0" } });
        render(
            <MemoryRouter>
                <SearchPageMapView
                    state={
                        {
                            ...INITIAL_STATE,
                            filters: { _rectype: { label: "Assets", value: "asset" } },
                            tablePreferences: { pageSize: 25 },
                            useMapView: true,
                            result: {
                                hits: { total: { value: 1, relation: "gte" }, hits: [hit] },
                                aggregations: {},
                            },
                        } as any
                    }
                    dispatch={jest.fn()}
                />
            </MemoryRouter>
        );
        fireEvent.click(screen.getByTestId("marker"));
        const links = assetLinks(screen.getByTestId("popup"));
        expect(links.length).toBeGreaterThan(0);
        for (const href of links) {
            expect(target(href)).toEqual({ databaseId: "db-a", assetId, filePath: null });
        }
    });
});
