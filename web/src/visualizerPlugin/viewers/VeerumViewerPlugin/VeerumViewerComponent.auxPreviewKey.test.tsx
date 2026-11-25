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
 * The point cloud branch names each file by the key the host gives (the full S3 key the file list
 * returns) through the shared `auxiliaryPreviewStreamUrl` helper. The auxiliary preview stream reads a
 * full key and an asset-relative path alike (#1855), so the viewer no longer cuts a leading assetId
 * segment off the key: that guess missed every asset whose location is not `{assetId}/` and named the
 * wrong file for one in a subfolder named like the asset.
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

    it("sends the full asset-bucket key as the host gives it", async () => {
        renderViewer({ multiFileKeys: ["asset-1/scans/pump.e57"] });

        await waitFor(() => expect((global as any).fetch).toHaveBeenCalled());

        expect(streamedKeys()).toEqual([
            "asset-1/scans/pump.e57/preview/PotreeViewer/metadata.json",
        ]);
        expect(((global as any).fetch as jest.Mock).mock.calls[0][0]).toBe(
            "https://vams.example.com/api/database/db1/assets/asset-1/auxiliaryPreviewAssets/" +
                "stream/asset-1/scans/pump.e57/preview/PotreeViewer/metadata.json"
        );
    });

    it("sends an asset-relative key from a host without the full key unchanged", async () => {
        renderViewer({ multiFileKeys: ["scans/pump.e57"] });

        await waitFor(() => expect((global as any).fetch).toHaveBeenCalled());

        expect(streamedKeys()).toEqual(["scans/pump.e57/preview/PotreeViewer/metadata.json"]);
    });

    it("sends the key of an asset in a bucket with a base prefix unchanged", async () => {
        renderViewer({ multiFileKeys: ["custom/base/asset-1/scan.las"] });

        await waitFor(() => expect((global as any).fetch).toHaveBeenCalled());

        expect(streamedKeys()).toEqual([
            "custom/base/asset-1/scan.las/preview/PotreeViewer/metadata.json",
        ]);
    });

    it("keeps a subfolder named like the asset in the key", async () => {
        renderViewer({ multiFileKeys: ["asset-1/asset-1/scan.las"] });

        await waitFor(() => expect((global as any).fetch).toHaveBeenCalled());

        expect(streamedKeys()).toEqual([
            "asset-1/asset-1/scan.las/preview/PotreeViewer/metadata.json",
        ]);
    });

    it("sends each file of a multi-file selection by its own key", async () => {
        renderViewer({ multiFileKeys: ["asset-1/a.laz", "asset-1/nested/b.ply"] });

        await waitFor(() => expect(((global as any).fetch as jest.Mock).mock.calls.length).toBe(2));

        expect(streamedKeys()).toEqual([
            "asset-1/a.laz/preview/PotreeViewer/metadata.json",
            "asset-1/nested/b.ply/preview/PotreeViewer/metadata.json",
        ]);
    });

    it("sends the single-file assetKey path by its full key, each segment encoded", async () => {
        renderViewer({ assetKey: "asset-1/scans/pump 1.e57" });

        await waitFor(() => expect((global as any).fetch).toHaveBeenCalled());

        expect(streamedKeys()).toEqual([
            "asset-1/scans/pump%201.e57/preview/PotreeViewer/metadata.json",
        ]);
    });
});
