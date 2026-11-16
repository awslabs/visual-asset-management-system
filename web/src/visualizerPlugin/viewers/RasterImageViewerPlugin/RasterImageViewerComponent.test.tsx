/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import RasterImageViewerComponent from "./RasterImageViewerComponent";
import { buildTga, buildTiff } from "./rasterFixtures.testdata";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const putImageData = jest.fn();

beforeAll(() => {
    (global as any).ImageData = class {
        constructor(public data: any, public width: number, public height: number) {}
    };
    HTMLCanvasElement.prototype.getContext = (() => ({ putImageData })) as any;
});

function respond(bytes: ArrayBuffer | Uint8Array, status = 200) {
    const body = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    return {
        ok: status < 400,
        status,
        headers: { get: () => null },
        body: undefined,
        arrayBuffer: async () => body.buffer.slice(body.byteOffset, body.byteOffset + body.length),
    } as any;
}

const baseProps = {
    assetId: "a1",
    databaseId: "d1",
    viewerMode: "collapse",
    onViewerModeChange: jest.fn(),
} as any;

describe("RasterImageViewerComponent", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/file?sig=1"]);
    });

    it("draws a decoded TGA to the canvas", async () => {
        const tga = buildTga(2, 2, [
            [255, 0, 0],
            [0, 255, 0],
            [0, 0, 255],
            [9, 9, 9],
        ]);
        (global as any).fetch = jest.fn().mockResolvedValue(respond(tga));
        render(<RasterImageViewerComponent {...baseProps} assetKey="/tex/wall.tga" />);

        const canvas = await screen.findByTestId("raster-viewer-canvas");
        expect(canvas.getAttribute("data-width")).toBe("2");
        expect(canvas.getAttribute("data-format")).toBe("tga");
        await waitFor(() => expect(putImageData).toHaveBeenCalled());
        expect(screen.getByTestId("raster-viewer-info").textContent).toContain("TGA · 2 × 2 px");
    });

    it("offers a page picker for a multi-page TIFF", async () => {
        const page = (v: number) => ({
            width: 2,
            height: 1,
            bps: 8,
            spp: 1,
            photometric: 1,
            data: new Uint8Array([v, v]),
        });
        (global as any).fetch = jest.fn().mockResolvedValue(respond(buildTiff([page(1), page(2)])));
        render(<RasterImageViewerComponent {...baseProps} assetKey="/scan.tif" />);

        expect(await screen.findByTestId("raster-viewer-page-picker")).toBeInTheDocument();
        expect(screen.getByTestId("raster-viewer-info").textContent).toContain("TIFF");
    });

    it("shows the generated preview with a notice above the size cap, without downloading", async () => {
        (global as any).fetch = jest.fn();
        render(
            <RasterImageViewerComponent
                {...baseProps}
                assetKey="/big.tif"
                fileInfo={{
                    filename: "big.tif",
                    key: "/big.tif",
                    size: 999999999,
                    previewFile: "/big.tif.previewFile.jpg",
                }}
                customParameters={{ maxDecodeBytes: 1000 }}
            />
        );

        expect(await screen.findByTestId("raster-viewer-fallback-preview")).toBeInTheDocument();
        expect(screen.getByTestId("raster-viewer-fallback-notice").textContent).toMatch(
            /larger than the .* the viewer opens\. Showing its generated preview instead\./
        );
        expect((global as any).fetch).not.toHaveBeenCalled();
        expect(mockDownloadAsset).toHaveBeenCalledTimes(1);
        expect(mockDownloadAsset.mock.calls[0][0].key).toBe("/big.tif.previewFile.jpg");
    });

    it("says so in plain words when over the cap and no preview exists", async () => {
        (global as any).fetch = jest.fn();
        render(
            <RasterImageViewerComponent
                {...baseProps}
                assetKey="/big.tga"
                fileInfo={{ filename: "big.tga", key: "/big.tga", size: 5000 } as any}
                customParameters={{ maxDecodeBytes: 1000 }}
            />
        );
        const notice = await screen.findByTestId("raster-viewer-fallback-notice");
        expect(notice.textContent).toMatch(/No preview has been generated/);
        expect(screen.queryByTestId("raster-viewer-fallback-preview")).toBeNull();
    });

    it("falls back when the file cannot be decoded", async () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
        (global as any).fetch = jest.fn().mockResolvedValue(respond(new Uint8Array(40)));
        render(
            <RasterImageViewerComponent
                {...baseProps}
                assetKey="/bad.tif"
                fileInfo={
                    {
                        filename: "bad.tif",
                        key: "/bad.tif",
                        previewFile: "/bad.previewFile.jpg",
                    } as any
                }
            />
        );
        expect(await screen.findByTestId("raster-viewer-fallback-preview")).toBeInTheDocument();
        warn.mockRestore();
    });

    it("names the missing permission and never falls back when the API refuses the download", async () => {
        mockDownloadAsset.mockResolvedValue([false, "Not Authorized", 403]);
        (global as any).fetch = jest.fn();
        render(
            <RasterImageViewerComponent
                {...baseProps}
                assetKey="/a.tif"
                fileInfo={
                    { filename: "a.tif", key: "/a.tif", previewFile: "/a.previewFile.jpg" } as any
                }
            />
        );
        expect(
            await screen.findByText("You do not have permission to view or download this file.")
        ).toBeInTheDocument();
        expect(screen.queryByTestId("raster-viewer-fallback-preview")).toBeNull();
        expect(mockDownloadAsset).toHaveBeenCalledTimes(1);
    });
});
