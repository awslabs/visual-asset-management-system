/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import CogMapViewerComponent, { filesToShow } from "./CogMapViewerComponent";
import viewerConfig from "../../config/viewerConfig.json";
import { admitsVisualizeSelection } from "../../core/viewerSelection";

const mockMapLoaded: { value: boolean } = { value: false };
jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        default: ReactActual.forwardRef((props: any, ref: any) => {
            ReactActual.useImperativeHandle(ref, () => ({
                getMap: () => (mockMapLoaded.value ? {} : null),
            }));
            ReactActual.useEffect(() => {
                if (mockMapLoaded.value) props.onLoad?.();
            }, []);
            return <div data-testid="map">{props.children}</div>;
        }),
        NavigationControl: () => null,
    };
});
jest.mock("../../../common/utils/maplibreWorker", () => ({}));
const mockConfig: { value: any } = { value: { locationServiceApiUrl: "https://maps/style" } };
jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: () => mockConfig.value },
}));
const mockLayers: { value: any } = { value: null };
const freshLayers = () => ({
    entries: [] as any[],
    notices: [],
    busy: 0,
    addFile: jest.fn(),
    remove: jest.fn(),
    setOpacity: jest.fn(),
    move: jest.fn(),
    zoomTo: jest.fn(() => false),
    dismissNotice: jest.fn(),
});
jest.mock("../../../common/cogRaster/useRasterLayers", () => ({
    useRasterLayers: () => mockLayers.value,
}));
beforeEach(() => {
    mockMapLoaded.value = false;
    mockLayers.value = freshLayers();
});

const base = {
    assetId: "a1",
    databaseId: "db",
    viewerMode: "wide",
    onViewerModeChange: () => undefined,
};

describe("filesToShow", () => {
    it("keeps the TIFFs of a selection, each under its own asset", () => {
        const files = filesToShow({
            ...base,
            multiFiles: [
                { filename: "a.tif", key: "a.tif", isDirectory: false, size: 10 },
                {
                    filename: "b.TIFF",
                    key: "b.TIFF",
                    isDirectory: false,
                    assetId: "a2",
                    databaseId: "db2",
                },
                { filename: "c.png", key: "c.png", isDirectory: false },
                { filename: "d", key: "d/", isDirectory: true },
            ],
        });
        expect(files).toEqual([
            { databaseId: "db", assetId: "a1", key: "a.tif", versionId: undefined, sizeBytes: 10 },
            {
                databaseId: "db2",
                assetId: "a2",
                key: "b.TIFF",
                versionId: undefined,
                sizeBytes: undefined,
            },
        ]);
    });
    it("keeps the version of a single file", () => {
        expect(filesToShow({ ...base, assetKey: "x.tif", versionId: "v1" })).toEqual([
            {
                databaseId: "db",
                assetId: "a1",
                key: "x.tif",
                versionId: "v1",
                assetVersionId: undefined,
            },
        ]);
    });
});

describe("CogMapViewerComponent layout (D2)", () => {
    it("wraps the layer list under the map instead of squeezing the map, with the map first in focus order", () => {
        render(<CogMapViewerComponent {...base} assetKey="x.tif" />);
        const root = screen.getByTestId("cog-map-viewer");
        const mapColumn = screen.getByTestId("cog-map-column");
        const listColumn = screen.getByTestId("cog-layer-column");
        expect(root.style.flexWrap).toBe("wrap");
        // The map is a flex item that may not shrink below its basis in a way that reaches zero: it
        // has its own height, a basis wider than a phone (so it wraps there), and a growth factor
        // that keeps it far larger than the list on a wide viewer.
        expect(mapColumn.style.flex).toBe("999 1 480px");
        expect(parseFloat(mapColumn.style.minWidth)).toBe(0);
        expect(mapColumn.style.minHeight).toBe("360px");
        expect(mapColumn.style.height).not.toBe("");
        expect(listColumn.style.flex).toBe("1 1 300px");
        expect(listColumn.style.overflowX).toBe("hidden");
        // Document order, which is tab order at every width, is the map then its list.
        expect(
            mapColumn.compareDocumentPosition(listColumn) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
    });
});

describe("CogMapViewerComponent", () => {
    it("frames the first raster once its extent is known, not before (D3)", () => {
        const layers = mockLayers.value;
        const entry = { id: "r1", label: "x.tif", framable: false, failed: null };
        layers.entries = [entry];
        const { rerender } = render(<CogMapViewerComponent {...base} assetKey="x.tif" />);
        // Not framable yet (a tile source whose extent is not loaded): nothing to zoom to.
        layers.zoomTo.mockReturnValue(false);
        expect(layers.zoomTo).not.toHaveBeenCalled();
        // The extent arrives: the entries change and framing happens then.
        layers.entries = [{ ...entry, framable: true }];
        layers.zoomTo.mockReturnValue(true);
        rerender(<CogMapViewerComponent {...base} assetKey="x.tif" />);
        expect(layers.zoomTo).toHaveBeenCalledWith("r1");
        // Once framed it does not frame again.
        layers.entries = [
            { ...entry, framable: true },
            { id: "r2", framable: true, failed: null },
        ];
        rerender(<CogMapViewerComponent {...base} assetKey="x.tif" />);
        expect(layers.zoomTo).toHaveBeenCalledTimes(1);
    });

    it("tries again when the first zoom finds no bounds", () => {
        const layers = mockLayers.value;
        layers.entries = [{ id: "r1", framable: true, failed: null }];
        layers.zoomTo.mockReturnValueOnce(false).mockReturnValue(true);
        const { rerender } = render(<CogMapViewerComponent {...base} assetKey="x.tif" />);
        layers.entries = [{ id: "r1", framable: true, failed: null }];
        rerender(<CogMapViewerComponent {...base} assetKey="x.tif" />);
        expect(layers.zoomTo).toHaveBeenCalledTimes(2);
    });
    it("asks for a pinned version and the latest file as two rasters (r3)", () => {
        mockMapLoaded.value = true;
        const file = { databaseId: "db", assetId: "a1", key: "a.tif", isDirectory: false, size: 5 };
        render(
            <CogMapViewerComponent
                {...base}
                assetKey="a.tif"
                multiFiles={[file, { ...file, versionId: "v2" }] as any}
            />
        );
        const calls = mockLayers.value.addFile.mock.calls.map((call: any[]) => call[0].versionId);
        expect(calls).toEqual([undefined, "v2"]);
    });
    it("renders the map and the layer list", () => {
        render(<CogMapViewerComponent {...base} assetKey="x.tif" />);
        expect(screen.getByTestId("cog-map-viewer")).toBeInTheDocument();
        expect(screen.getByText("Raster layers")).toBeInTheDocument();
    });
    it("says when location services are not configured", () => {
        mockConfig.value = {};
        render(<CogMapViewerComponent {...base} assetKey="x.tif" />);
        expect(screen.getByText(/location services are not configured/)).toBeInTheDocument();
        mockConfig.value = { locationServiceApiUrl: "https://maps/style" };
    });
});

describe("geotiff-map-viewer configuration", () => {
    const viewer: any = (viewerConfig as any).viewers.find(
        (v: any) => v.id === "geotiff-map-viewer"
    );

    it("is gated on location services and offered for TIFFs, one or many", () => {
        expect(viewer.featuresEnabledRestriction).toEqual(["LOCATIONSERVICES"]);
        expect(viewer.supportedExtensions).toEqual([".tif", ".tiff"]);
        expect(admitsVisualizeSelection(viewer, [".tif"], false)).toBe(true);
        expect(admitsVisualizeSelection(viewer, [".tif", ".tiff"], true)).toBe(true);
    });

    it("has a name distinct from every other viewer", () => {
        const names = (viewerConfig as any).viewers.map((v: any) => v.name);
        expect(names.filter((name: string) => name === viewer.name)).toHaveLength(1);
    });
});
