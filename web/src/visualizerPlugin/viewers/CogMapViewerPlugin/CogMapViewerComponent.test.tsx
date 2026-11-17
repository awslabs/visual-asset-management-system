/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import CogMapViewerComponent, { filesToShow } from "./CogMapViewerComponent";
import viewerConfig from "../../config/viewerConfig.json";
import { admitsVisualizeSelection } from "../../core/viewerSelection";

jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    return {
        __esModule: true,
        default: ReactActual.forwardRef((props: any, ref: any) => {
            ReactActual.useImperativeHandle(ref, () => ({ getMap: () => null }));
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
jest.mock("../../../common/cogRaster/useRasterLayers", () => ({
    useRasterLayers: () => ({
        entries: [],
        notices: [],
        busy: 0,
        addFile: jest.fn(),
        remove: jest.fn(),
        setOpacity: jest.fn(),
        move: jest.fn(),
        zoomTo: jest.fn(),
        dismissNotice: jest.fn(),
    }),
}));

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

describe("CogMapViewerComponent", () => {
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
