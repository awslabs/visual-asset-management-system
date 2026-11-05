/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The resolver is the only place the Three.js viewer turns a name written inside a model into a
 * download. These tests pin its path rules (nothing above the asset root, nothing absolute,
 * nothing with a scheme), its limits, and that its URL modifier never lets an unresolved name
 * become a request.
 */

import {
    AssetDependencyResolver,
    buildDependencyUrl,
    DEPENDENCY_LIMITS,
    describeUnloaded,
    isolatedUrlModifier,
    normalizeDependencyPath,
    PLACEHOLDER_IMAGE_DATA_URL,
    placeholderImageBytes,
    stripUsdAssetDelimiters,
} from "./assetDependencyResolver";

const ASSET = "asset-1";
const CONTEXT = {
    assetId: ASSET,
    databaseId: "db-1",
    apiEndpoint: "https://api.example.test/",
    getAuthHeader: () => Promise.resolve("Bearer token"),
};

type FakeFile = { status?: number; bytes?: number; contentLength?: number };

/** Installs a fetch that serves `files` by asset-relative key and records every URL requested. */
function installFetch(files: Record<string, FakeFile>): string[] {
    const requested: string[] = [];
    global.fetch = jest.fn(async (url: string) => {
        requested.push(url);
        const path = decodeURIComponent(
            url.replace(/^.*\/download\/stream\//, "").replace(/\?.*$/, "")
        );
        const file = files[path];
        if (!file) {
            return { ok: false, status: 404, headers: { get: () => null } };
        }
        const status = file.status ?? 200;
        return {
            ok: status < 400,
            status,
            headers: {
                get: (name: string) =>
                    name === "content-length" && file.contentLength !== undefined
                        ? String(file.contentLength)
                        : null,
            },
            body: { cancel: jest.fn(() => Promise.resolve()) },
            arrayBuffer: () => Promise.resolve(new ArrayBuffer(file.bytes ?? 4)),
        };
    }) as any;
    return requested;
}

describe("normalizeDependencyPath", () => {
    const base = "models/car/scene.usda";

    it.each([
        ["tex.png", "models/car/tex.png"],
        ["./tex.png", "models/car/tex.png"],
        ["textures/tex.png", "models/car/textures/tex.png"],
        ["../shared/tex.png", "models/shared/tex.png"],
        ["../../root.png", "root.png"],
        ["textures\\wood.png", "models/car/textures/wood.png"],
        ["my texture.png", "models/car/my texture.png"],
        ["tëxturé.png", "models/car/tëxturé.png"],
        ["a%20b.png", "models/car/a%20b.png"],
        ["@./layer.usda@", "models/car/layer.usda"],
        ["@@@weird@name.usda@@@", "models/car/weird@name.usda"],
        ["@./layer.usda@</Root/Geom>", "models/car/layer.usda"],
        ["pkg.usdz[inner.usda]", "models/car/pkg.usdz"],
    ])("resolves %s inside the asset", (ref, key) => {
        expect(normalizeDependencyPath(ref, base, ASSET)).toEqual({ kind: "key", key });
    });

    it.each([
        ["../../../escape.png", "outside-asset"],
        ["/etc/passwd", "outside-asset"],
        ["C:\\textures\\wood.png", "outside-asset"],
        ["C:/textures/wood.png", "outside-asset"],
        ["//evil.example/x.png", "outside-asset"],
        ["\\\\server\\share\\x.png", "outside-asset"],
        ["http://example.com/x.png", "external"],
        ["https://example.com/x.png", "external"],
        ["file:///tmp/x.png", "external"],
        ["omniverse://server/x.usd", "external"],
    ])("rejects %s (%s)", (ref, reason) => {
        expect(normalizeDependencyPath(ref, base, ASSET)).toEqual({ kind: "rejected", reason });
    });

    it("never climbs into another asset through the asset id segment", () => {
        // The asset id is not part of the asset's own tree, so `..` cannot step over it.
        expect(
            normalizeDependencyPath("../other-asset/x.usda", `${ASSET}/scene.usda`, ASSET)
        ).toEqual({ kind: "rejected", reason: "outside-asset" });
        expect(normalizeDependencyPath("tex.png", `${ASSET}/a/scene.usda`, ASSET)).toEqual({
            kind: "key",
            key: "a/tex.png",
        });
    });

    it("treats UDIM tokens as not fetched", () => {
        expect(normalizeDependencyPath("tex.<UDIM>.png", base, ASSET)).toEqual({ kind: "udim" });
        expect(normalizeDependencyPath("tex.<udim>.png", base, ASSET)).toEqual({ kind: "udim" });
    });

    it("passes inline URLs and ignores empty names", () => {
        expect(normalizeDependencyPath("data:image/png;base64,AA", base, ASSET)).toEqual({
            kind: "inline",
        });
        expect(normalizeDependencyPath("blob:https://x/1", base, ASSET)).toEqual({
            kind: "inline",
        });
        expect(normalizeDependencyPath("  ", base, ASSET)).toEqual({ kind: "empty" });
        expect(normalizeDependencyPath("./", base, ASSET)).toEqual({ kind: "empty" });
        expect(normalizeDependencyPath("textures/..", base, ASSET)).toEqual({ kind: "empty" });
    });
});

describe("stripUsdAssetDelimiters", () => {
    it("removes @, @@@, prim paths and package layers", () => {
        expect(stripUsdAssetDelimiters("@a.usda@")).toBe("a.usda");
        expect(stripUsdAssetDelimiters("@@@a@b.usda@@@")).toBe("a@b.usda");
        expect(stripUsdAssetDelimiters("@a.usda@</Prim>")).toBe("a.usda");
        expect(stripUsdAssetDelimiters("a.usdz[b.usda]")).toBe("a.usdz");
        expect(stripUsdAssetDelimiters("plain.png")).toBe("plain.png");
    });
});

describe("buildDependencyUrl", () => {
    it("encodes every segment and passes the asset version", () => {
        expect(
            buildDependencyUrl("tex/my file#1?.png", { ...CONTEXT, assetVersionId: "v 2" })
        ).toBe(
            "https://api.example.test/database/db-1/assets/asset-1/download/stream/tex/my%20file%231%3F.png?assetVersionId=v%202"
        );
    });

    it("omits the query without an asset version", () => {
        expect(buildDependencyUrl("a.png", CONTEXT)).not.toContain("?");
    });
});

describe("AssetDependencyResolver.resolveDependencies", () => {
    const originalFetch = global.fetch;

    afterEach(() => {
        global.fetch = originalFetch;
    });

    it("downloads in-asset files with the auth header and keys them by the name as written", async () => {
        installFetch({ "m/tex.png": { bytes: 10 } });
        const resolver = new AssetDependencyResolver(CONTEXT);

        const result = await resolver.resolveDependencies("m/scene.obj", ["./tex.png"]);

        expect(result.resolved.get("./tex.png")?.key).toBe("m/tex.png");
        expect(result.resolved.get("./tex.png")?.bytes.byteLength).toBe(10);
        expect((global.fetch as jest.Mock).mock.calls[0][1].headers.Authorization).toBe(
            "Bearer token"
        );
        expect(resolver.resolvedCount).toBe(1);
    });

    it("never requests a rejected name", async () => {
        const requested = installFetch({});
        const resolver = new AssetDependencyResolver(CONTEXT);

        const result = await resolver.resolveDependencies("scene.usda", [
            "../../other-asset/x.usda",
            "https://example.com/x.png",
            "/abs.png",
        ]);

        expect(requested).toEqual([]);
        expect(result.rejected).toHaveLength(3);
        expect(resolver.notLoaded().map((item) => item.reason)).toEqual([
            "outside-asset",
            "external",
            "outside-asset",
        ]);
    });

    it("records a missing file once and does not retry another spelling", async () => {
        const requested = installFetch({});
        const resolver = new AssetDependencyResolver(CONTEXT);

        const result = await resolver.resolveDependencies("a/scene.usda", ["Tex.png", "./Tex.png"]);

        expect(requested).toHaveLength(1);
        expect(result.missing).toEqual(["Tex.png", "./Tex.png"]);
        expect(resolver.notLoaded()).toEqual([{ path: "a/Tex.png", reason: "not-found" }]);
    });

    it("records UDIM names without fetching", async () => {
        const requested = installFetch({});
        const resolver = new AssetDependencyResolver(CONTEXT);

        await resolver.resolveDependencies("scene.usda", ["tex.<UDIM>.png"]);

        expect(requested).toEqual([]);
        expect(resolver.notLoaded()).toEqual([{ path: "tex.<UDIM>.png", reason: "udim" }]);
    });

    it("stops at the file count limit and keeps what loaded", async () => {
        const files: Record<string, FakeFile> = {};
        for (let i = 0; i < 5; i++) files[`t${i}.png`] = { bytes: 1 };
        const requested = installFetch(files);
        const resolver = new AssetDependencyResolver({ ...CONTEXT, limits: { maxFiles: 3 } });

        const result = await resolver.resolveDependencies(
            "scene.obj",
            Object.keys(files).map((name) => name)
        );

        expect(requested).toHaveLength(3);
        expect(result.resolved.size).toBe(3);
        expect(resolver.limitReached).toBe(true);
        expect(resolver.notLoaded().filter((item) => item.reason === "limit")).toHaveLength(2);
    });

    it("stops at the total byte limit", async () => {
        installFetch({ "a.png": { bytes: 6 }, "b.png": { bytes: 6 }, "c.png": { bytes: 1 } });
        const resolver = new AssetDependencyResolver({ ...CONTEXT, limits: { maxTotalBytes: 10 } });

        await resolver.resolveDependencies("scene.obj", ["a.png"]);
        await resolver.resolveDependencies("scene.obj", ["b.png"]);
        const later = await resolver.resolveDependencies("scene.obj", ["c.png"]);

        expect(resolver.getFetched("a.png")).toBeDefined();
        expect(resolver.getFetched("b.png")).toBeUndefined();
        expect(later.resolved.size).toBe(0);
        expect(resolver.limitReached).toBe(true);
    });

    it("skips a single file above the size limit, by header or by body", async () => {
        installFetch({
            "declared.png": { bytes: 1, contentLength: 100 },
            "actual.png": { bytes: 100 },
            "ok.png": { bytes: 1 },
        });
        const resolver = new AssetDependencyResolver({ ...CONTEXT, limits: { maxFileBytes: 50 } });

        const result = await resolver.resolveDependencies("scene.obj", [
            "declared.png",
            "actual.png",
            "ok.png",
        ]);

        expect(Array.from(result.resolved.keys())).toEqual(["ok.png"]);
        expect(resolver.limitReached).toBe(false);
    });

    it("refuses files deeper than the reference depth limit", async () => {
        const requested = installFetch({ "deep.usda": { bytes: 1 } });
        const resolver = new AssetDependencyResolver(CONTEXT);

        const result = await resolver.resolveDependencies("scene.usda", ["deep.usda"], {
            depth: DEPENDENCY_LIMITS.maxDepth + 1,
        });

        expect(requested).toEqual([]);
        expect(result.missing).toEqual(["deep.usda"]);
    });

    it("downloads a file once however often it is named, which breaks cycles", async () => {
        const requested = installFetch({ "a.usda": { bytes: 1 } });
        const resolver = new AssetDependencyResolver(CONTEXT);

        await resolver.resolveDependencies("scene.usda", ["a.usda"]);
        await resolver.resolveDependencies("sub/x.usda", ["../a.usda"]);

        expect(requested).toHaveLength(1);
        expect(resolver.markVisited("a.usda")).toBe(true);
        expect(resolver.markVisited("a.usda")).toBe(false);
    });

    it("reports progress per downloaded file", async () => {
        installFetch({ "a.png": {}, "b.png": {} });
        const resolver = new AssetDependencyResolver(CONTEXT);
        const progress = jest.fn();

        await resolver.resolveDependencies("s.obj", ["a.png", "b.png"], { onProgress: progress });

        expect(progress).toHaveBeenLastCalledWith(2, 2);
    });

    it("propagates an abort instead of recording the file as missing", async () => {
        global.fetch = jest.fn(() =>
            Promise.reject(Object.assign(new Error("aborted"), { name: "AbortError" }))
        ) as any;
        const resolver = new AssetDependencyResolver(CONTEXT);

        await expect(resolver.resolveDependencies("s.obj", ["a.png"])).rejects.toThrow("aborted");
    });
});

describe("AssetDependencyResolver URL modifier", () => {
    const originalFetch = global.fetch;
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    let counter = 0;

    beforeEach(() => {
        counter = 0;
        URL.createObjectURL = jest.fn(() => `blob:test/${++counter}`);
        URL.revokeObjectURL = jest.fn();
    });

    afterEach(() => {
        global.fetch = originalFetch;
        URL.createObjectURL = originalCreate;
        URL.revokeObjectURL = originalRevoke;
    });

    it("maps resolved names to one blob URL and answers everything else with the placeholder", async () => {
        installFetch({ "m/tex.png": {} });
        const resolver = new AssetDependencyResolver(CONTEXT);
        const { resolved } = await resolver.resolveDependencies("m/a.dae", ["tex.png"]);
        const modify = resolver.createUrlModifier(resolved, "m/a.dae");

        expect(modify("tex.png")).toBe("blob:test/1");
        expect(modify("./tex.png")).toBe("blob:test/1");
        expect(modify("blob:existing")).toBe("blob:existing");
        expect(modify("data:image/png;base64,AA")).toBe("data:image/png;base64,AA");
        expect(modify("missing.png")).toBe(PLACEHOLDER_IMAGE_DATA_URL);
        expect(modify("https://example.com/x.png")).toBe(PLACEHOLDER_IMAGE_DATA_URL);
        expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
        expect(resolver.notLoaded()).toEqual([
            { path: "m/missing.png", reason: "not-found" },
            { path: "https://example.com/x.png", reason: "external" },
        ]);
    });

    it("matches a percent-encoded request to the file it names", async () => {
        installFetch({ "my tex.png": {} });
        const resolver = new AssetDependencyResolver(CONTEXT);
        const { resolved } = await resolver.resolveDependencies("a.obj", ["my tex.png"]);

        expect(resolver.createUrlModifier(resolved, "a.obj")("my%20tex.png")).toBe("blob:test/1");
    });

    it("revokes every blob URL it created", async () => {
        installFetch({ "a.png": {}, "b.png": {} });
        const resolver = new AssetDependencyResolver(CONTEXT);
        const { resolved } = await resolver.resolveDependencies("x.obj", ["a.png", "b.png"]);
        const modify = resolver.createUrlModifier(resolved, "x.obj");
        modify("a.png");
        modify("b.png");

        resolver.revokeAll();

        expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test/1");
        expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test/2");
        expect(resolver.createdBlobUrls).toEqual([]);
    });

    it("records requests without resolving them", () => {
        const resolver = new AssetDependencyResolver(CONTEXT);
        const requests: string[] = [];
        const modify = resolver.createRecordingModifier(requests);

        expect(modify("tex.png")).toBe(PLACEHOLDER_IMAGE_DATA_URL);
        expect(modify("blob:x")).toBe("blob:x");
        expect(requests).toEqual(["tex.png"]);
    });

    it("isolatedUrlModifier passes only inline URLs", () => {
        expect(isolatedUrlModifier("blob:x")).toBe("blob:x");
        expect(isolatedUrlModifier("textures/a.png")).toBe(PLACEHOLDER_IMAGE_DATA_URL);
    });
});

describe("placeholder and descriptions", () => {
    it("the placeholder is a PNG", () => {
        const bytes = placeholderImageBytes();
        expect(Array.from(bytes.slice(0, 4))).toEqual([0x89, 0x50, 0x4e, 0x47]);
    });

    it("describes each reason in words", () => {
        expect(
            describeUnloaded([
                { path: "a.png", reason: "not-found" },
                { path: "../x", reason: "outside-asset" },
            ])
        ).toEqual(["a.png (not found in this asset)", "../x (outside this asset)"]);
    });
});
