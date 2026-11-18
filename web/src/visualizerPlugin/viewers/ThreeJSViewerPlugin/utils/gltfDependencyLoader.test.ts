/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { preloadGLTFDependencies } from "./gltfDependencyLoader";
import { downloadAsset } from "../../../../services/APIService";
import { clearPresignedUrlCache } from "../../../core/presignedFetch";

jest.mock("../../../../services/APIService", () => ({ downloadAsset: jest.fn() }));

const mockedDownloadAsset = downloadAsset as jest.Mock;

const GLTF = {
    buffers: [{ uri: "model.bin" }, { uri: "data:application/octet-stream;base64,AAAA" }],
    images: [{ uri: "textures/wood.png" }, { uri: "../shared/sky.jpg" }],
};

function encode(json: unknown): ArrayBuffer {
    return new TextEncoder().encode(JSON.stringify(json)).buffer as ArrayBuffer;
}

describe("preloadGLTFDependencies (#1623)", () => {
    const originalFetch = global.fetch;
    const originalCreate = URL.createObjectURL;

    beforeEach(() => {
        clearPresignedUrlCache();
        mockedDownloadAsset.mockReset();
        mockedDownloadAsset.mockImplementation(async ({ key }: { key: string }) => [
            true,
            `https://bucket.s3.example.test/${key}?X-Amz-Signature=sig`,
        ]);
        global.fetch = jest.fn(async () => ({
            ok: true,
            status: 200,
            arrayBuffer: async () => new ArrayBuffer(4),
        })) as any;
        URL.createObjectURL = jest.fn(() => "blob:test");
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });

    afterEach(() => {
        global.fetch = originalFetch;
        URL.createObjectURL = originalCreate;
        jest.restoreAllMocks();
    });

    it("reads each buffer and image through a presigned URL, with no stream URL or Authorization", async () => {
        const result = await preloadGLTFDependencies(encode(GLTF), {
            assetId: "asset-1",
            databaseId: "db-1",
            baseFileKey: "asset-1/models/scene.gltf",
            assetVersionId: "av1",
        });

        const keys = mockedDownloadAsset.mock.calls.map((call) => call[0].key).sort();
        // The asset id prefix is dropped, as the streaming route took it.
        expect(keys).toEqual(["models/model.bin", "models/textures/wood.png", "shared/sky.jpg"]);
        for (const [call] of mockedDownloadAsset.mock.calls) {
            expect(call).toMatchObject({
                assetId: "asset-1",
                databaseId: "db-1",
                assetVersionId: "av1",
                downloadType: "assetFile",
            });
            expect(call.versionId).toBeUndefined();
        }
        expect(global.fetch).toHaveBeenCalledTimes(3);
        for (const [url, options] of (global.fetch as jest.Mock).mock.calls) {
            expect(url).not.toContain("/download/stream/");
            expect(options.headers).toBeUndefined();
            expect(options.credentials).toBeUndefined();
        }
        expect(result.blobUrls).toHaveLength(3);
    });

    it("hands the viewer's abort signal to every presign request", async () => {
        const controller = new AbortController();

        await preloadGLTFDependencies(encode(GLTF), {
            assetId: "asset-1",
            databaseId: "db-1",
            baseFileKey: "asset-1/models/scene.gltf",
            signal: controller.signal,
        });

        for (const [call] of mockedDownloadAsset.mock.calls) {
            expect(call.signal).toBe(controller.signal);
        }
    });

    it("keeps going when one dependency is refused", async () => {
        mockedDownloadAsset.mockImplementation(async ({ key }: { key: string }) =>
            key.endsWith("wood.png")
                ? [false, "Forbidden"]
                : [true, `https://bucket.s3.example.test/${key}?X-Amz-Signature=sig`]
        );
        jest.spyOn(console, "warn").mockImplementation(() => undefined);

        const result = await preloadGLTFDependencies(encode(GLTF), {
            assetId: "asset-1",
            databaseId: "db-1",
            baseFileKey: "asset-1/models/scene.gltf",
        });

        expect(result.blobUrls).toHaveLength(2);
    });
});
