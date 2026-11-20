/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `installPresignedStreamReads` against a minimal Cesium stand-in, so these tests run wherever the
 * web unit tests run. `presignedStreamReads.realCesium.test.ts` covers the same hook against the
 * real Cesium bundle, which CI does not install.
 */
import { installPresignedStreamReads } from "./presignedStreamReads";
import { clearPresignedUrlCache } from "../../core/presignedFetch";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const API = "https://api.example/api/";
const PREFIX = `${API}database/db1/assets/a1/download/stream/`;
const scope = { streamPrefix: PREFIX, databaseId: "db1", assetId: "a1" };

class RequestErrorEvent {
    constructor(public statusCode?: number, public response?: any) {}
}

let originalLoadWithXhr: jest.Mock;
let originalCreateImage: jest.Mock;
let Cesium: any;

beforeEach(() => {
    clearPresignedUrlCache();
    mockDownloadAsset.mockReset();
    mockDownloadAsset.mockImplementation(async ({ key }: any) => [
        true,
        `https://bucket.s3.amazonaws.com/${key}?X-Amz-Signature=s`,
    ]);
    originalLoadWithXhr = jest.fn(() => ({ abort: jest.fn() }));
    originalCreateImage = jest.fn();
    Cesium = {
        RequestErrorEvent,
        Resource: {
            _Implementations: {
                loadWithXhr: originalLoadWithXhr,
                createImage: originalCreateImage,
            },
        },
    };
});

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("keys with dot segments", () => {
    const traversal = [
        "a1/%2e%2e/other/x.json",
        "a1/%2E%2E/x.json",
        "a1/..%2Fother%2Fx.json",
        "a1%2F..%2Fx.json",
        "a1/./x.json",
        "a1/%2e/x.json",
        "../x.json",
    ];

    it.each(traversal)("leaves %s to the stream route and sends no presign call", async (path) => {
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const impl = Cesium.Resource._Implementations;
        const deferred = { resolve: jest.fn(), reject: jest.fn() };
        impl.loadWithXhr(`${PREFIX}${path}`, "text", "GET", undefined, {}, deferred);
        impl.createImage({ url: `${PREFIX}${path}` }, false, deferred);
        await tick();
        uninstall();
        expect(mockDownloadAsset).not.toHaveBeenCalled();
        expect(originalLoadWithXhr).toHaveBeenCalledTimes(1);
        expect(originalLoadWithXhr.mock.calls[0][0]).toBe(`${PREFIX}${path}`);
        expect(originalCreateImage).toHaveBeenCalledTimes(1);
    });

    it("still presigns an ordinary nested key and one with dots inside a name", async () => {
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const deferred = { resolve: jest.fn(), reject: jest.fn() };
        const impl = Cesium.Resource._Implementations;
        impl.loadWithXhr(
            `${PREFIX}a1/t/v1.2/tile..b3dm`,
            "arraybuffer",
            "GET",
            undefined,
            {},
            deferred
        );
        await tick();
        uninstall();
        expect(mockDownloadAsset.mock.calls[0][0].key).toBe("a1/t/v1.2/tile..b3dm");
    });
});

describe("image requests", () => {
    it("presigns, reads the signed URL and keeps the request's own URL", async () => {
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const request: any = { url: `${PREFIX}a1/tex/b.png` };
        const deferred = { resolve: jest.fn(), reject: jest.fn() };
        originalCreateImage.mockImplementation((r: any) => {
            expect(r.url).toContain("bucket.s3.amazonaws.com");
        });
        Cesium.Resource._Implementations.createImage(request, false, deferred);
        await tick();
        uninstall();
        expect(originalCreateImage).toHaveBeenCalledTimes(1);
        expect(request.url).toBe(`${PREFIX}a1/tex/b.png`);
    });

    it("starts no read when Cesium cancels the request during the presign call", async () => {
        let release: (value: any) => void = () => undefined;
        mockDownloadAsset.mockImplementation(
            () => new Promise((resolve) => (release = resolve as any))
        );
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const request: any = { url: `${PREFIX}a1/tex/c.png` };
        const deferred = { resolve: jest.fn(), reject: jest.fn() };
        Cesium.Resource._Implementations.createImage(request, false, deferred);
        expect(typeof request.cancelFunction).toBe("function");
        request.cancelFunction();
        expect(mockDownloadAsset.mock.calls[0][0].signal.aborted).toBe(true);
        release([true, "https://bucket.s3.amazonaws.com/c.png?X-Amz-Signature=s"]);
        await tick();
        uninstall();
        expect(originalCreateImage).not.toHaveBeenCalled();
        expect(deferred.reject).not.toHaveBeenCalled();
    });
});
