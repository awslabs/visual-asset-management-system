/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `installPresignedStreamReads` against the real Cesium bundle the viewer ships
 * (`public/viewers/cesium/Cesium.js`, built by `customInstalls/cesium/cesiumInstall.js`). Only the
 * network transport is replaced (a fake XMLHttpRequest); Cesium's own request path (Resource,
 * RequestScheduler, tileset loading, retries) runs unchanged, so these tests show which Cesium
 * request paths the hook covers. The suite skips with a warning when the bundle is not installed
 * and fails when REQUIRE_CESIUM_BUNDLE=1.
 */
import fs from "fs";
import path from "path";
import { installPresignedStreamReads } from "./presignedStreamReads";
import { clearPresignedUrlCache } from "../../core/presignedFetch";
import missingFile from "../../core/fileNotFoundBody.testdata.json";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const BUNDLE = path.resolve(__dirname, "../../../../public/viewers/cesium/Cesium.js");
// CI installs web dependencies with `npm ci --ignore-scripts` for the unit tests, so the bundle (built
// by the Cesium postinstall) is absent there and this suite is skipped; the Cesium stand-in suite
// `presignedStreamReads.test.ts` runs instead. Set REQUIRE_CESIUM_BUNDLE=1 to make a missing bundle
// a failure (any run that installs with scripts, for example `npm ci && npx jest`).
const bundleInstalled = fs.existsSync(BUNDLE);
const bundleRequired = process.env.REQUIRE_CESIUM_BUNDLE === "1";
const describeIfBundle = bundleInstalled ? describe : describe.skip;

if (!bundleInstalled) {
    if (bundleRequired) {
        it("has the Cesium bundle installed (REQUIRE_CESIUM_BUNDLE=1)", () => {
            throw new Error(
                `Cesium bundle not found at ${BUNDLE}; run npm ci with install scripts`
            );
        });
    } else {
        // eslint-disable-next-line no-console
        console.warn(
            `SKIPPED: real-Cesium hook tests, no bundle at ${BUNDLE} (set REQUIRE_CESIUM_BUNDLE=1 to fail)`
        );
    }
}

const API = "https://api.example/api/";
const PREFIX = `${API}database/db1/assets/a1/download/stream/`;
const S3 = "https://bucket.s3.amazonaws.com/";

interface Sent {
    method: string;
    url: string;
    headers: Record<string, string>;
}

let sent: Sent[];
/** Answers a request the fake XMLHttpRequest got: `undefined` means 404. */
let serve: (request: Sent) => { status?: number; body?: string | Uint8Array } | undefined;

class FakeXhr {
    static instances: FakeXhr[] = [];
    method = "";
    url = "";
    headers: Record<string, string> = {};
    responseType = "";
    status = 0;
    response: any = null;
    responseText = "";
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    aborted = false;
    open(method: string, url: string) {
        this.method = method;
        this.url = url;
        FakeXhr.instances.push(this);
    }
    setRequestHeader(name: string, value: string) {
        this.headers[name] = value;
    }
    overrideMimeType() {
        return undefined;
    }
    getAllResponseHeaders() {
        return "";
    }
    abort() {
        this.aborted = true;
    }
    send() {
        const request = { method: this.method, url: this.url, headers: this.headers };
        sent.push(request);
        setTimeout(() => {
            if (this.aborted) return;
            const answer = serve(request) ?? { status: 404, body: "" };
            this.status = answer.status ?? 200;
            const bytes =
                typeof answer.body === "string"
                    ? new TextEncoder().encode(answer.body)
                    : answer.body ?? new Uint8Array();
            if (this.responseType === "arraybuffer") {
                this.response = bytes.buffer.slice(
                    bytes.byteOffset,
                    bytes.byteOffset + bytes.byteLength
                );
            } else if (this.responseType === "blob") {
                this.response = new Blob([bytes as BlobPart]);
            } else {
                this.response = new TextDecoder().decode(bytes);
                this.responseText = this.response;
            }
            this.onload?.();
        }, 0);
    }
}

function streamUrl(key: string, assetVersionId?: string) {
    const encoded = key
        .split("/")
        .map((s) => encodeURIComponent(s))
        .join("/");
    return `${PREFIX}${encoded}${assetVersionId ? `?assetVersionId=${assetVersionId}` : ""}`;
}

/** A SigV4-shaped presigned URL for `key` (the cache keeps only URLs that state their lifetime). */
function presigned(key: string, assetVersionId?: string) {
    const date = new Date().toISOString().replace(/[-:]|\.\d{3}/g, "");
    return (
        `${S3}${encodeURI(key)}?X-Amz-Date=${date}&X-Amz-Expires=900` +
        `&X-Amz-Signature=sig-${assetVersionId ?? "latest"}`
    );
}

describeIfBundle("installPresignedStreamReads with the real Cesium build", () => {
    let Cesium: any;
    let originalXhr: any;

    beforeAll(() => {
        // jsdom lacks the web stream classes the bundle touches while it initializes.
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const streams = require("stream/web");
        for (const name of ["TransformStream", "ReadableStream", "WritableStream"]) {
            (globalThis as any)[name] ??= streams[name];
        }
        // The bundle is a classic script that sets the global `Cesium`.
        // eslint-disable-next-line no-eval
        (0, eval)(fs.readFileSync(BUNDLE, "utf8"));
        Cesium = (globalThis as any).Cesium;
    });

    beforeEach(() => {
        sent = [];
        FakeXhr.instances = [];
        originalXhr = (globalThis as any).XMLHttpRequest;
        (globalThis as any).XMLHttpRequest = FakeXhr;
        clearPresignedUrlCache();
        mockDownloadAsset.mockReset();
        mockDownloadAsset.mockImplementation(async ({ key, assetVersionId }: any) => [
            true,
            presigned(key, assetVersionId),
        ]);
        serve = (request) =>
            request.url.startsWith(S3) ? { body: `body of ${request.url}` } : undefined;
        jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });

    afterEach(() => {
        (globalThis as any).XMLHttpRequest = originalXhr;
        jest.restoreAllMocks();
    });

    const scope = { streamPrefix: PREFIX, databaseId: "db1", assetId: "a1" };
    const authHeaders = () => ({
        Authorization: "Bearer t",
        "Content-Type": "application/json",
    });
    const resourceFor = (url: string, extra: any = {}) =>
        new Cesium.Resource({ url, headers: authHeaders(), ...extra });

    it("reads tileset JSON, derived children, binary and text through presigned URLs", async () => {
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const root = resourceFor(streamUrl("a1/tiles/tileset.json", "v7"));
        const json = await root.fetchJson().catch((e: any) => e);
        expect(json).toBeDefined();
        // Children are derived from the root: relative URI, query kept as the tileset loader does.
        const child = root.getDerivedResource({
            url: "sub/ext.json",
            preserveQueryParameters: true,
        });
        expect(child.url).toBe(streamUrl("a1/tiles/sub/ext.json", "v7"));
        await child.fetchArrayBuffer();
        const tile = root.getDerivedResource({
            url: "../models/tile 1+2.glb",
            preserveQueryParameters: true,
        });
        await tile.fetchArrayBuffer();
        await root.getDerivedResource({ url: "subtrees/0.0.0.subtree" }).fetchArrayBuffer();
        uninstall();

        // Every request went to S3; none went to the stream route.
        expect(sent.length).toBe(4);
        expect(sent.every((s) => s.url.startsWith(S3))).toBe(true);
        // No Authorization, and no Content-Type that would force a CORS preflight.
        expect(sent.every((s) => !("Authorization" in s.headers))).toBe(true);
        expect(sent.every((s) => !("Content-Type" in s.headers))).toBe(true);
        const keys = mockDownloadAsset.mock.calls.map((c) => [c[0].key, c[0].assetVersionId]);
        expect(keys).toEqual([
            ["a1/tiles/tileset.json", "v7"],
            ["a1/tiles/sub/ext.json", "v7"],
            ["a1/models/tile 1+2.glb", "v7"],
            ["a1/tiles/subtrees/0.0.0.subtree", "v7"],
        ]);
        expect(mockDownloadAsset.mock.calls[0][0]).toMatchObject({
            databaseId: "db1",
            assetId: "a1",
            downloadType: "assetFile",
        });
    });

    it("loads a tileset with Cesium3DTileset.fromUrl and its tile content requests", async () => {
        serve = (request) => {
            if (!request.url.startsWith(S3)) return undefined;
            if (request.url.includes("tileset.json")) {
                return {
                    body: JSON.stringify({
                        asset: { version: "1.1" },
                        geometricError: 10,
                        root: {
                            boundingVolume: { sphere: [0, 0, 0, 10] },
                            geometricError: 5,
                            refine: "ADD",
                            content: { uri: "nested/inner.json" },
                        },
                    }),
                };
            }
            return { body: "x" };
        };
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const resource = resourceFor(streamUrl("a1/t/tileset.json", "v1"), { retryAttempts: 1 });
        const tileset = await Cesium.Cesium3DTileset.fromUrl(resource);
        uninstall();
        expect(tileset.root).toBeDefined();
        expect(sent.map((s) => s.url.startsWith(S3))).toEqual([true]);
        expect(sent[0].headers.Authorization).toBeUndefined();
    });

    it("covers image fetches by blob (headers) and by element (no headers)", async () => {
        const created: string[] = [];
        // Cesium's image-element path: the original createImage receives the presigned URL.
        const original = Cesium.Resource._Implementations.createImage;
        Cesium.Resource._Implementations.createImage = (request: any, ...rest: any[]) => {
            created.push(request.url);
            rest[1].resolve({ fake: "image" });
        };
        const uninstall = installPresignedStreamReads(Cesium, scope);
        // With request headers Cesium reads the image as a blob first (an XHR the hook handles).
        const blobXhrBefore = sent.length;
        const blobPromise = resourceFor(streamUrl("a1/tex/a.png")).fetchBlob();
        await blobPromise;
        expect(sent.length).toBe(blobXhrBefore + 1);
        expect(sent[blobXhrBefore].url.startsWith(S3)).toBe(true);
        // Without headers Cesium uses an image element, which never touches XHR.
        const image = await new Cesium.Resource({ url: streamUrl("a1/tex/b.png") }).fetchImage();
        uninstall();
        Cesium.Resource._Implementations.createImage = original;
        expect(image).toEqual({ fake: "image" });
        expect(created).toEqual([presigned("a1/tex/b.png")]);
    });

    it("passes every other URL and method through untouched", async () => {
        const uninstall = installPresignedStreamReads(Cesium, scope);
        serve = () => ({ body: "{}" });
        await new Cesium.Resource({
            url: "https://elsewhere.example/x.json",
            headers: authHeaders(),
        }).fetchJson();
        // Another asset's stream URL is not this viewer's scope.
        await resourceFor(`${API}database/db1/assets/OTHER/download/stream/k.json`).fetchJson();
        await resourceFor(streamUrl("a1/h.json"))
            .head()
            .catch(() => undefined);
        uninstall();
        expect(mockDownloadAsset).not.toHaveBeenCalled();
        expect(sent[0].headers.Authorization).toBe("Bearer t");
        expect(sent[1].url).toContain("/assets/OTHER/");
        expect(sent[2].method).toBe("HEAD");
    });

    it("leaves a key with an encoded dot segment to the stream route", async () => {
        const uninstall = installPresignedStreamReads(Cesium, scope);
        serve = () => ({ body: "{}" });
        await resourceFor(`${PREFIX}a1/%2e%2e/other/x.json`)
            .fetchJson()
            .catch(() => undefined);
        uninstall();
        expect(mockDownloadAsset).not.toHaveBeenCalled();
        expect(sent[0].url).toContain("/download/stream/");
    });

    it("restores the original functions on unmount and is reference counted", async () => {
        const impl = Cesium.Resource._Implementations;
        const loadBefore = impl.loadWithXhr;
        const imageBefore = impl.createImage;
        const first = installPresignedStreamReads(Cesium, scope);
        const wrapped = impl.loadWithXhr;
        expect(wrapped).not.toBe(loadBefore);
        const second = installPresignedStreamReads(Cesium, {
            ...scope,
            assetId: "a2",
            streamPrefix: `${API}database/db1/assets/a2/download/stream/`,
        });
        expect(impl.loadWithXhr).toBe(wrapped);

        // Both viewers' scopes are served by the one wrapper.
        serve = () => ({ body: "{}" });
        await resourceFor(`${API}database/db1/assets/a2/download/stream/k.json`).fetchJson();
        expect(mockDownloadAsset.mock.calls[0][0]).toMatchObject({ assetId: "a2", key: "k.json" });

        first();
        expect(impl.loadWithXhr).toBe(wrapped);
        // After the first viewer left, its scope is no longer rewritten.
        serve = () => ({ body: "{}" });
        mockDownloadAsset.mockClear();
        await resourceFor(streamUrl("a1/z.json")).fetchJson();
        expect(mockDownloadAsset).not.toHaveBeenCalled();
        second();
        second();
        expect(impl.loadWithXhr).toBe(loadBefore);
        expect(impl.createImage).toBe(imageBefore);
    });

    it("cancels an in-flight read when Cesium aborts the request", async () => {
        let release: (value: any) => void = () => undefined;
        mockDownloadAsset.mockImplementation(
            () => new Promise((resolve) => (release = resolve as any))
        );
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const resource = resourceFor(streamUrl("a1/slow.bin"));
        const promise = resource.fetchArrayBuffer();
        // Cesium cancels through the request's cancelFunction, which calls abort on our handle.
        resource.request.cancelFunction();
        release([true, presigned("a1/slow.bin")]);
        await new Promise((r) => setTimeout(r, 20));
        uninstall();
        expect(sent).toHaveLength(0);
        // Cesium does not settle a cancelled request's promise from the loader.
        const settled = await Promise.race([
            promise.then(() => "settled").catch(() => "settled"),
            new Promise((r) => setTimeout(() => r("pending"), 20)),
        ]);
        expect(settled).toBe("pending");
    });

    it("reads a reused expired URL once more through a fresh presign", async () => {
        const uninstall = installPresignedStreamReads(Cesium, scope);
        let reads = 0;
        serve = (request) => {
            if (!request.url.startsWith(S3)) return undefined;
            reads += 1;
            return reads === 1 ? { status: 403, body: "expired" } : { body: "fresh" };
        };
        // A URL just issued cannot have expired: its 403 is reported unavailable, with no retry.
        const failure = await resourceFor(streamUrl("a1/e.bin"))
            .fetchArrayBuffer()
            .catch((e: any) => e);
        expect(failure.message).toMatch(/expired or the file is unavailable/);
        expect(mockDownloadAsset).toHaveBeenCalledTimes(1);

        // A cached URL that S3 now refuses is presigned again and read again.
        reads = 0;
        serve = (request) => {
            if (!request.url.startsWith(S3)) return undefined;
            reads += 1;
            return reads === 1 ? { status: 403, body: "expired" } : { body: "fresh" };
        };
        mockDownloadAsset.mockClear();
        const bytes = await resourceFor(streamUrl("a1/e.bin"))
            .fetchArrayBuffer()
            .catch((e: any) => e);
        uninstall();
        // The earlier failure left the first URL cached, so this call reused it, then refreshed.
        expect(mockDownloadAsset).toHaveBeenCalledTimes(1);
        expect(Object.prototype.toString.call(bytes)).toBe("[object ArrayBuffer]");
        expect(reads).toBe(2);
    });

    it("surfaces the API's refusal as a RequestErrorEvent with its status and message", async () => {
        mockDownloadAsset.mockResolvedValue([
            false,
            "x",
            400,
            "Asset is quarantined and cannot be downloaded",
        ]);
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const error: any = await resourceFor(streamUrl("a1/q.json"))
            .fetchJson()
            .catch((e: any) => e);
        uninstall();
        expect(error).toBeInstanceOf(Cesium.RequestErrorEvent);
        expect(error.statusCode).toBe(400);
        expect(JSON.parse(error.response).message).toBe(
            "Asset is quarantined and cannot be downloaded"
        );
        expect(sent).toHaveLength(0);
    });

    it("fails a tile the download route reports as missing as a 404 without a retry", async () => {
        // As the viewer's refreshAuthOnDenial: only a 401 or 403 is retried.
        const retryCallback = jest.fn(
            async (_r: any, e: any) => e.statusCode === 401 || e.statusCode === 403
        );
        mockDownloadAsset.mockResolvedValue([
            false,
            "x",
            missingFile.statusCode,
            missingFile.body.message,
        ]);
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const error: any = await resourceFor(streamUrl("a1/gone.glb"), {
            retryAttempts: 1,
            retryCallback,
        })
            .fetchArrayBuffer()
            .catch((e: any) => e);
        uninstall();
        expect(error).toBeInstanceOf(Cesium.RequestErrorEvent);
        expect(error.statusCode).toBe(404);
        expect(retryCallback).toHaveBeenCalledTimes(1);
        expect(retryCallback.mock.results[0].value).resolves.toBe(false);
        expect(mockDownloadAsset).toHaveBeenCalledTimes(1);
        expect(sent).toHaveLength(0);
    });

    it("keeps the viewer's 401/403 retry: the retried read presigns again", async () => {
        const retryCallback = jest.fn(async () => true);
        mockDownloadAsset
            .mockResolvedValueOnce([false, "denied", 403])
            .mockImplementation(async ({ key }: any) => [true, presigned(key)]);
        const uninstall = installPresignedStreamReads(Cesium, scope);
        const result = await resourceFor(streamUrl("a1/r.bin"), {
            retryAttempts: 1,
            retryCallback,
        }).fetchArrayBuffer();
        uninstall();
        expect(retryCallback).toHaveBeenCalledTimes(1);
        expect((retryCallback.mock.calls[0] as any[])[1].statusCode).toBe(403);
        expect(Object.prototype.toString.call(result)).toBe("[object ArrayBuffer]");
        expect(mockDownloadAsset).toHaveBeenCalledTimes(2);
    });
});
