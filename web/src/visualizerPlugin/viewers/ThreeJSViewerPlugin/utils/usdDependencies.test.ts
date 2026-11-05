/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * three.js composes a multi-file USD scene only from the files inside one .usdz. These tests pin
 * how the viewer finds the files a layer names (ASCII and binary), stores each under the key the
 * composer looks it up by, keeps unresolved images from turning into requests, and checks a
 * .usdz's declared size before anything is extracted.
 */

import { AssetDependencyResolver, PLACEHOLDER_IMAGE_DATA_URL } from "./assetDependencyResolver";
import {
    buildUsdPackage,
    checkUsdzSize,
    composerKey,
    extractUsdAssetPaths,
    isUsdcBytes,
    isUsdzBytes,
    USDZ_LIMITS,
} from "./usdDependencies";

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const text = (value: string) => encoder.encode(value);

/**
 * Stands in for fflate: a "zip" is the ZIP magic followed by JSON of the entries in order, which
 * is enough to check the order, names and contents buildUsdPackage writes.
 */
const fakeFflate = {
    zipSync: jest.fn((files: Record<string, Uint8Array>) => {
        const body = JSON.stringify(
            Object.keys(files).map((name) => [name, decoder.decode(files[name])])
        );
        return new Uint8Array([0x50, 0x4b, 0x03, 0x04, ...Array.from(text(body))]);
    }),
    unzipSync: jest.fn((bytes: Uint8Array, opts?: any) => {
        const entries: Array<[string, string]> = JSON.parse(decoder.decode(bytes.slice(4)));
        const files: Record<string, Uint8Array> = {};
        for (const [name, content] of entries) {
            const data = text(content);
            if (opts?.filter && !opts.filter({ name, originalSize: data.byteLength })) {
                continue;
            }
            files[name] = data;
        }
        return files;
    }),
};

function packageEntries(buffer: ArrayBuffer): Array<[string, string]> {
    return JSON.parse(decoder.decode(new Uint8Array(buffer).slice(4)));
}

function fakeZip(entries: Array<[string, string]>): Uint8Array {
    return new Uint8Array([0x50, 0x4b, 0x03, 0x04, ...Array.from(text(JSON.stringify(entries)))]);
}

function installFetch(files: Record<string, string>): string[] {
    const requested: string[] = [];
    global.fetch = jest.fn(async (url: string) => {
        const key = decodeURIComponent(url.replace(/^.*\/download\/stream\//, ""));
        requested.push(key);
        const content = files[key];
        if (content === undefined) {
            return { ok: false, status: 404, headers: { get: (): string | null => null } };
        }
        return {
            ok: true,
            status: 200,
            headers: { get: (): string | null => null },
            arrayBuffer: () => Promise.resolve(text(content).buffer),
        };
    }) as any;
    return requested;
}

function newResolver() {
    return new AssetDependencyResolver({
        assetId: "asset-1",
        databaseId: "db-1",
        apiEndpoint: "https://api.example.test/",
        getAuthHeader: () => Promise.resolve("Bearer t"),
    });
}

const USDA_FIXTURE = `#usda 1.0
(
    subLayers = [@./lighting.usda@, @@@odd@name.usda@@@]
)
def Xform "Root" (
    prepend references = @./geom/chair.usda@</Chair>
    payload = @props/lamp.usdc@
)
{
    def Shader "Tex"
    {
        asset inputs:file = @textures/wood.png@
        string doc = "plain text without asset paths"
    }
}
`;

describe("extractUsdAssetPaths", () => {
    it("finds references, payloads, sublayers and asset attributes in ASCII layers", () => {
        expect(extractUsdAssetPaths(text(USDA_FIXTURE)).sort()).toEqual(
            [
                "./lighting.usda",
                "odd@name.usda",
                "./geom/chair.usda",
                "props/lamp.usdc",
                "textures/wood.png",
            ].sort()
        );
    });

    it("reads binary layers through the USDC parser, including its token table", () => {
        const crate = text("PXR-USDC\0\0\0\0binary");
        class FakeUSDCParser {
            tokens = ["Root", "xformOp:translate", "./refs/part.usdc", "tex/metal.jpg"];
            parseData() {
                return {
                    specsByPath: {
                        "/Root/Shader.inputs:file": { fields: { default: "tex/diffuse.png" } },
                        "/Root/Mesh.points": { fields: { default: [0, 1, 2] } },
                    },
                };
            }
        }

        expect(isUsdcBytes(crate)).toBe(true);
        expect(extractUsdAssetPaths(crate, FakeUSDCParser).sort()).toEqual(
            ["./refs/part.usdc", "tex/diffuse.png", "tex/metal.jpg"].sort()
        );
    });

    it("ignores assetInfo identifiers, which name the asset rather than a composed file", () => {
        const layer = `#usda 1.0
def Xform "Ball" (
    assetInfo = {
        asset identifier = @assets/Ball/Ball.usd@
        string name = "Ball"
    }
    payload = @./Ball_payload.usd@</Ball>
)
{
}
`;
        expect(extractUsdAssetPaths(text(layer))).toEqual(["./Ball_payload.usd"]);

        class IdentifierParser {
            tokens = ["assets/Ball/Ball.usd", "./Ball.geom.usd"];
            parseData() {
                return {
                    specsByPath: {
                        "/Ball": { fields: { assetInfo: { identifier: "assets/Ball/Ball.usd" } } },
                    },
                };
            }
        }
        expect(extractUsdAssetPaths(text("PXR-USDC"), IdentifierParser)).toEqual([
            "./Ball.geom.usd",
        ]);
    });

    it("returns nothing for a binary layer without a parser or one it cannot read", () => {
        const crate = text("PXR-USDC");
        expect(extractUsdAssetPaths(crate)).toEqual([]);
        class Broken {
            parseData() {
                throw new Error("bad crate");
            }
        }
        jest.spyOn(console, "warn").mockImplementation(() => undefined);
        expect(extractUsdAssetPaths(crate, Broken)).toEqual([]);
    });
});

describe("composerKey", () => {
    it("joins like three.js's USDComposer, without normalizing", () => {
        expect(composerKey("./a.usda", "root.usda")).toBe("a.usda");
        expect(composerKey("tex/a.png", "root.usda")).toBe("tex/a.png");
        expect(composerKey("../tex/a.png", "geom/chair.usda")).toBe("geom/../tex/a.png");
        expect(composerKey("./b.usda", "geom/chair.usda")).toBe("geom/b.usda");
        expect(composerKey("https://x/a.png", "geom/chair.usda")).toBe("https://x/a.png");
    });
});

describe("checkUsdzSize", () => {
    const zipOf = (count: number, size: number) => ({
        bytes: new Uint8Array(1024),
        unzipSync: jest.fn((_data: Uint8Array, opts: any) => {
            for (let i = 0; i < count; i++) {
                expect(opts.filter({ name: `f${i}`, originalSize: size })).toBe(false);
            }
            return {};
        }),
    });

    it("accepts a normal package without inflating anything", () => {
        const { bytes, unzipSync } = zipOf(3, 100);
        expect(() => checkUsdzSize(bytes, unzipSync)).not.toThrow();
        expect(unzipSync).toHaveBeenCalledTimes(1);
    });

    it("rejects a package that declares too many uncompressed bytes", () => {
        const { bytes, unzipSync } = zipOf(1, USDZ_LIMITS.expansionAllowanceBytes + 4 * 1024 + 1);
        expect(() => checkUsdzSize(bytes, unzipSync)).toThrow(/expands to/);
    });

    it("rejects a package with too many entries", () => {
        const { bytes, unzipSync } = zipOf(USDZ_LIMITS.maxEntries + 1, 0);
        expect(() => checkUsdzSize(bytes, unzipSync)).toThrow(/entries/);
    });

    it("reports an unreadable archive", () => {
        expect(() =>
            checkUsdzSize(new Uint8Array(4), () => {
                throw new Error("invalid zip data");
            })
        ).toThrow(/could not be read: invalid zip data/);
    });
});

describe("buildUsdPackage", () => {
    const originalFetch = global.fetch;

    afterEach(() => {
        global.fetch = originalFetch;
        jest.clearAllMocks();
    });

    it("returns the root unchanged when it names no files", async () => {
        const requested = installFetch({});
        const root = text('#usda 1.0\ndef Xform "A" {}\n');

        const result = await buildUsdPackage({
            rootKey: "m/a.usda",
            rootBytes: root,
            resolver: newResolver(),
            fflate: fakeFflate,
        });

        expect(result.repacked).toBe(false);
        expect(new Uint8Array(result.buffer)).toEqual(root);
        expect(requested).toEqual([]);
    });

    it("packages referenced layers and textures, root first, under the composer's keys", async () => {
        const requested = installFetch({
            "m/geom/chair.usda":
                "#usda 1.0\nasset inputs:file = @../tex/wood.png@\nref = @../scene.usda@\n",
            "m/tex/wood.png": "PNGDATA",
        });
        const root = text(
            "#usda 1.0\nprepend references = @./geom/chair.usda@\nasset inputs:file = @tex/wood.png@\n" +
                "asset inputs:file = @missing.png@\nasset inputs:file = @https://example.com/x.png@\n" +
                "asset inputs:file = @tex/<UDIM>.png@\n"
        );
        const resolver = newResolver();

        const result = await buildUsdPackage({
            rootKey: "m/scene.usda",
            rootBytes: root,
            resolver,
            fflate: fakeFflate,
        });

        expect(result.repacked).toBe(true);
        const entries = packageEntries(result.buffer);
        const names = entries.map(([name]) => name);
        expect(names[0]).toBe("scene.usda");
        expect(names).toEqual(
            expect.arrayContaining([
                "geom/chair.usda",
                "tex/wood.png",
                "geom/../tex/wood.png",
                "missing.png",
                "https://example.com/x.png",
            ])
        );
        // The cycle back to the root is not packaged again or downloaded.
        expect(names).not.toContain("geom/../scene.usda");
        const content = Object.fromEntries(entries);
        expect(content["geom/../tex/wood.png"]).toBe("PNGDATA");
        expect(content["missing.png"]).not.toBe("PNGDATA");
        // Each in-asset file is downloaded once; nothing external, rejected or UDIM is requested.
        expect(requested.sort()).toEqual(
            ["m/geom/chair.usda", "m/tex/wood.png", "m/missing.png"].sort()
        );
        expect(resolver.notLoaded()).toEqual(
            expect.arrayContaining([
                { path: "m/missing.png", reason: "not-found" },
                { path: "https://example.com/x.png", reason: "external" },
                { path: "tex/<UDIM>.png", reason: "udim" },
            ])
        );
    });

    it("stores the placeholder PNG for images that did not load", async () => {
        installFetch({});
        const result = await buildUsdPackage({
            rootKey: "a.usda",
            rootBytes: text("asset inputs:file = @gone.png@"),
            resolver: newResolver(),
            fflate: fakeFflate,
        });

        const placeholderBytes = atob(PLACEHOLDER_IMAGE_DATA_URL.split(",")[1]);
        const stored = Object.fromEntries(packageEntries(result.buffer))["gone.png"];
        expect(stored.length).toBeGreaterThan(0);
        expect(placeholderBytes.startsWith("\x89PNG")).toBe(true);
    });

    it("keeps a .usdz's own entries and resolves only what lies outside it", async () => {
        const requested = installFetch({ "m/shared/lib.usda": "#usda 1.0\n" });
        const usdz = fakeZip([
            ["root.usda", "prepend references = @../shared/lib.usda@\nasset inputs:file = @t.png@"],
            ["t.png", "INNER"],
        ]);

        expect(isUsdzBytes(usdz)).toBe(true);
        const result = await buildUsdPackage({
            rootKey: "m/pkg/model.usdz",
            rootBytes: usdz,
            resolver: newResolver(),
            fflate: fakeFflate,
        });

        const names = packageEntries(result.buffer).map(([name]) => name);
        expect(names).toEqual(["root.usda", "t.png", "../shared/lib.usda"]);
        expect(requested).toEqual(["m/shared/lib.usda"]);
    });

    it("size-checks a .usdz before extracting it", async () => {
        installFetch({});
        const huge = {
            ...fakeFflate,
            unzipSync: jest.fn((_bytes: Uint8Array, opts?: any) => {
                opts?.filter?.({ name: "x.usda", originalSize: 1024 * 1024 * 1024 });
                return {};
            }),
        };

        await expect(
            buildUsdPackage({
                rootKey: "big.usdz",
                rootBytes: fakeZip([["x.usda", ""]]),
                resolver: newResolver(),
                fflate: huge,
            })
        ).rejects.toThrow(/expands to/);
        expect(huge.unzipSync).toHaveBeenCalledTimes(1);
    });

    it("does not download nested .usdz packages, which three.js cannot compose", async () => {
        const requested = installFetch({ "inner.usdz": "x" });
        const resolver = newResolver();

        await buildUsdPackage({
            rootKey: "a.usda",
            rootBytes: text("prepend references = @inner.usdz[root.usda]@"),
            resolver,
            fflate: fakeFflate,
        });

        expect(requested).toEqual([]);
        expect(resolver.notLoaded()).toEqual([
            { path: "inner.usdz[root.usda]", reason: "unsupported" },
        ]);
    });

    it("stops following references at the depth limit", async () => {
        const files: Record<string, string> = {};
        for (let i = 1; i <= 12; i++) files[`l${i}.usda`] = `ref = @l${i + 1}.usda@`;
        const requested = installFetch(files);
        const resolver = newResolver();

        await buildUsdPackage({
            rootKey: "l0.usda",
            rootBytes: text("ref = @l1.usda@"),
            resolver,
            fflate: fakeFflate,
        });

        expect(requested).toHaveLength(8);
        expect(resolver.limitReached).toBe(true);
    });
});
