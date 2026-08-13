/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import VeerumViewerComponent from "./VeerumViewerComponent";

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: () => ({ api: "https://vams.example.com/api/" }) },
}));

jest.mock("../../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn().mockResolvedValue("Bearer token"),
}));

jest.mock("./VeerumPanel", () => ({ __esModule: true, default: () => null }));

const mockAdd = jest.fn().mockResolvedValue(undefined);
jest.mock("./dependencies", () => ({
    VeerumDependencyManager: {
        loadVeerum: jest.fn().mockResolvedValue({
            VeerumViewer: () => null,
            PointCloudModel: class {
                name = "";
            },
            TileModel: class {
                name = "";
            },
        }),
    },
}));

/**
 * The aux preview stream endpoint prepends {databaseId}/{assetLocationKey} to the proxy path
 * (streamAuxiliaryPreviewAsset.resolve_asset_file_path), and the writer key is
 * {databaseId}/{assetFileKey}/preview (executionRecords.aux_preview_file_prefix). The proxy path
 * must therefore be asset-RELATIVE — a leading assetId segment duplicates the asset location.
 */
describe("VeerumViewerComponent auxiliary preview key", () => {
    const controller = {
        add: mockAdd,
        zoomCameraToObject: jest.fn().mockResolvedValue(undefined),
        setModelVisuals: jest.fn(),
        dispose: jest.fn(),
    };

    beforeEach(() => {
        jest.clearAllMocks();
        mockAdd.mockResolvedValue(undefined);
        (window as any).ReactDOM = {
            render: (element: any) => {
                element.props.viewerControllerRef.current = controller;
            },
        };
        (global as any).fetch = jest.fn().mockResolvedValue({ ok: true, status: 200 });
    });

    const streamedKeys = () =>
        ((global as any).fetch as jest.Mock).mock.calls.map(
            (call: any[]) => String(call[0]).split("/auxiliaryPreviewAssets/stream/")[1]
        );

    const renderViewer = (props: { assetKey?: string; multiFileKeys?: string[] }) =>
        render(
            <VeerumViewerComponent
                assetId="asset-1"
                databaseId="db1"
                viewerMode="veerum-viewer"
                onViewerModeChange={jest.fn()}
                {...props}
            />
        );

    it("strips the leading assetId segment from the full asset-bucket key", async () => {
        renderViewer({ multiFileKeys: ["asset-1/scans/pump.e57"] });

        await waitFor(() => expect((global as any).fetch).toHaveBeenCalled());

        expect(streamedKeys()).toEqual(["scans/pump.e57/preview/PotreeViewer/metadata.json"]);
        expect(((global as any).fetch as jest.Mock).mock.calls[0][0]).toBe(
            "https://vams.example.com/api/database/db1/assets/asset-1/auxiliaryPreviewAssets/" +
                "stream/scans/pump.e57/preview/PotreeViewer/metadata.json"
        );
    });

    it("leaves an already asset-relative key untouched", async () => {
        renderViewer({ multiFileKeys: ["scans/pump.e57"] });

        await waitFor(() => expect((global as any).fetch).toHaveBeenCalled());

        expect(streamedKeys()).toEqual(["scans/pump.e57/preview/PotreeViewer/metadata.json"]);
    });

    it("preserves a custom asset base prefix that only contains the assetId deeper in the key", async () => {
        renderViewer({ multiFileKeys: ["custom/base/asset-1/scan.las"] });

        await waitFor(() => expect((global as any).fetch).toHaveBeenCalled());

        expect(streamedKeys()).toEqual([
            "custom/base/asset-1/scan.las/preview/PotreeViewer/metadata.json",
        ]);
    });

    it("strips the assetId from every file of a multi-file selection", async () => {
        renderViewer({ multiFileKeys: ["asset-1/a.laz", "asset-1/nested/b.ply"] });

        await waitFor(() => expect(((global as any).fetch as jest.Mock).mock.calls.length).toBe(2));

        expect(streamedKeys()).toEqual([
            "a.laz/preview/PotreeViewer/metadata.json",
            "nested/b.ply/preview/PotreeViewer/metadata.json",
        ]);
    });

    it("strips the assetId from the single-file assetKey path", async () => {
        renderViewer({ assetKey: "asset-1/scans/pump.e57" });

        await waitFor(() => expect((global as any).fetch).toHaveBeenCalled());

        expect(streamedKeys()).toEqual(["scans/pump.e57/preview/PotreeViewer/metadata.json"]);
    });
});
