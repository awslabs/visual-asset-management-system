/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import RasterImageViewerComponent from "./RasterImageViewerComponent";
import { buildRleTga, buildTga, buildTiff } from "./rasterFixtures.testdata";

// `import.meta.url` cannot be evaluated by jest's CommonJS transform, so the worker factory is
// replaced; without a worker the viewer decodes on the page, which is the path under test here.
const mockCreateWorker = jest.fn();
jest.mock("./createRasterWorker", () => ({ createRasterWorker: () => mockCreateWorker() }));

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

/** Makes a stand-in worker report `ready` once the session installs its first message handler. */
function announceReady(worker: any) {
    let handler: any = null;
    Object.defineProperty(worker, "onmessage", {
        configurable: true,
        get: () => handler,
        set: (fn) => {
            const first = handler === null;
            handler = fn;
            if (first && fn) setTimeout(() => handler({ data: { type: "ready" } }), 0);
        },
    });
}

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
        mockCreateWorker.mockReturnValue(null);
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

    it.each([
        [401, "Your session has expired. Sign in again to view this file."],
        [404, "This file or version could not be found."],
        [410, "This file version has been archived and is not available."],
    ])("shows the shared message for a %i and never opens a preview", async (status, message) => {
        mockDownloadAsset.mockResolvedValue([false, "refused", status]);
        (global as any).fetch = jest.fn();
        render(
            <RasterImageViewerComponent
                {...baseProps}
                assetKey="/a.tga"
                fileInfo={{ filename: "a.tga", key: "/a.tga", previewFile: "/p.jpg" } as any}
            />
        );
        expect(await screen.findByText(message)).toBeInTheDocument();
        expect(screen.queryByTestId("raster-viewer-fallback-preview")).toBeNull();
        expect(mockDownloadAsset).toHaveBeenCalledTimes(1);
    });

    it("labels the canvas with the file name", async () => {
        const tga = buildTga(1, 1, [[1, 2, 3]]);
        (global as any).fetch = jest.fn().mockResolvedValue(respond(tga));
        render(<RasterImageViewerComponent {...baseProps} assetKey="/tex/wall.tga" />);
        const canvas = await screen.findByTestId("raster-viewer-canvas");
        expect(canvas.getAttribute("role")).toBe("img");
        expect(canvas.getAttribute("aria-label")).toBe("TGA image wall.tga");
    });

    it("aborts the download when the viewer unmounts", async () => {
        let signal: AbortSignal | undefined;
        (global as any).fetch = jest.fn((_url: string, init?: RequestInit) => {
            signal = init?.signal as AbortSignal;
            return new Promise(() => {});
        });
        const view = render(<RasterImageViewerComponent {...baseProps} assetKey="/a.tga" />);
        await waitFor(() => expect(signal).toBeDefined());
        expect(signal!.aborted).toBe(false);
        view.unmount();
        expect(signal!.aborted).toBe(true);
    });

    it("says the download failed, without a preview, when the network fails", async () => {
        (global as any).fetch = jest.fn().mockRejectedValue(new TypeError("Failed to fetch"));
        render(
            <RasterImageViewerComponent
                {...baseProps}
                assetKey="/a.tga"
                fileInfo={{ filename: "a.tga", key: "/a.tga", previewFile: "/p.jpg" } as any}
            />
        );
        expect(await screen.findByText(/could not be downloaded/)).toBeInTheDocument();
        expect(screen.queryByTestId("raster-viewer-fallback-preview")).toBeNull();
    });

    it("shows the fallback notice for a truncated run-length TGA", async () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
        const file = buildRleTga(2, 2, [
            [1, 1, 1],
            [2, 2, 2],
            [3, 3, 3],
            [4, 4, 4],
        ]);
        (global as any).fetch = jest
            .fn()
            .mockResolvedValue(respond(file.slice(0, file.length - 4)));
        render(<RasterImageViewerComponent {...baseProps} assetKey="/cut.tga" />);
        const notice = await screen.findByTestId("raster-viewer-fallback-notice");
        expect(notice.textContent).toMatch(/truncated/);
        warn.mockRestore();
    });

    it("decodes in the worker when one can be created, and falls back when it stops", async () => {
        const tga = buildTga(2, 1, [
            [255, 0, 0],
            [0, 0, 255],
        ]);
        // A stand-in worker that answers with the real engine, as the bundled worker does.
        const { RasterEngine } = await import("./rasterEngine");
        const UTIF = (await import("utif2")).default as any;
        const TGA = (await import("tga-js")).default as any;
        const pako = (await import("pako")) as any;
        const engine = new RasterEngine({ utif: UTIF, tga: TGA, pako });
        const fake: any = {
            terminate: jest.fn(),
            postMessage(message: any) {
                setTimeout(() => {
                    if (message.type === "open") {
                        const pages = engine.open(message.buffer, message.kind, message.limits);
                        fake.onmessage({ data: { id: message.id, ok: true, pages } });
                    } else {
                        const r = engine.decode(message.index);
                        fake.onmessage({ data: { id: message.id, ok: true, ...r } });
                    }
                }, 0);
            },
        };
        announceReady(fake);
        mockCreateWorker.mockReturnValue(fake);
        (global as any).fetch = jest.fn().mockResolvedValue(respond(tga));
        const view = render(<RasterImageViewerComponent {...baseProps} assetKey="/w.tga" />);
        const canvas = await screen.findByTestId("raster-viewer-canvas");
        expect(canvas.getAttribute("data-width")).toBe("2");
        view.unmount();
        expect(fake.terminate).toHaveBeenCalled();

        // A worker that dies (out of memory) ends in the fallback notice, not a hung spinner.
        const dying: any = {
            terminate: jest.fn(),
            postMessage() {
                setTimeout(() => dying.onerror({ preventDefault() {} }), 0);
            },
        };
        announceReady(dying);
        mockCreateWorker.mockReturnValue(dying);
        const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
        render(<RasterImageViewerComponent {...baseProps} assetKey="/x.tga" />);
        expect(
            (await screen.findAllByTestId("raster-viewer-fallback-notice")).pop()!.textContent
        ).toMatch(/decoder stopped unexpectedly/);
        warn.mockRestore();
    });
});
