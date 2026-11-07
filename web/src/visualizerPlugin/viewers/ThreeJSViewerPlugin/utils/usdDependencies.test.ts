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

import {
    AssetDependencyResolver,
    describeUnloaded,
    PLACEHOLDER_IMAGE_DATA_URL,
} from "./assetDependencyResolver";
import {
    buildUsdPackage,
    checkUsdzSize,
    collectUsdLayerPaths,
    composerKey,
    extractUsdAssetPaths,
    isUsdcBytes,
    isUsdzBytes,
    USDZ_LIMITS,
} from "./usdDependencies";

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const text = (value: string) => encoder.encode(value);

/** USDC value type numbers, as three.js's USDCParser reads them from a crate's field table. */
const USDC_TYPE = {
    String: 10,
    Token: 11,
    AssetPath: 12,
    Dictionary: 31,
    StringVector: 50,
    PayloadListOp: 55,
};

/**
 * Stands in for three.js's USDCParser after parseData(): the field table (each field's name token
 * and value type), the token table that holds every string the crate stores, the decoded specs,
 * and _readValue(), which decodes one field's value.
 */
function fakeUsdcParser(fields: Array<[name: string, type: number, value: any]>) {
    return class {
        tokens: string[] = [];
        fields: Array<{ tokenIndex: number; valueRep: { typeEnum: number; index: number } }> = [];
        parseData() {
            const specFields: Record<string, any> = {};
            fields.forEach(([name, type, value], index) => {
                this.tokens.push(name);
                const flat = typeof value === "object" ? Object.values(value).flat() : [value];
                flat.forEach((item) => typeof item === "string" && this.tokens.push(item));
                this.fields.push({
                    tokenIndex: this.tokens.indexOf(name),
                    valueRep: { typeEnum: type, index },
                });
                specFields[name] = value;
            });
            return { specsByPath: { "/": { specType: 7, fields: specFields } } };
        }
        _readValue(valueRep: { index: number }) {
            return fields[valueRep.index][2];
        }
    };
}

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

    it("reads only the asset-path-typed fields of a binary layer", () => {
        const crate = text("PXR-USDC\0\0\0\0binary");
        const doc =
            "Generated from Composed Stage of root layer /home/someone/Teapot/geo/UtahTeapot.usd\n";
        expect(isUsdcBytes(crate)).toBe(true);
        expect(
            extractUsdAssetPaths(
                crate,
                fakeUsdcParser([
                    ["documentation", USDC_TYPE.String, doc],
                    ["comment", USDC_TYPE.String, "see textures/notes.png"],
                    ["defaultPrim", USDC_TYPE.Token, "UtahTeapot"],
                    ["default", USDC_TYPE.Token, "tex/token-not-asset.png"],
                    ["default", USDC_TYPE.AssetPath, "tex/diffuse.png"],
                    ["default", USDC_TYPE.AssetPath, ["tex/a.png", "@tex/b.jpg@"]],
                    ["customData", USDC_TYPE.Dictionary, { file: "dict/x.png" }],
                ])
            ).sort()
        ).toEqual(["tex/a.png", "tex/b.jpg", "tex/diffuse.png"]);
    });

    it("finds nothing in a usdcat-flattened binary layer, whose doc names the source layer", () => {
        const doc =
            "Generated from Composed Stage of root layer /home/someone/Teapot/geo/UtahTeapot.usd\n";
        expect(
            extractUsdAssetPaths(
                text("PXR-USDC"),
                fakeUsdcParser([
                    ["defaultPrim", USDC_TYPE.Token, "UtahTeapot"],
                    ["upAxis", USDC_TYPE.Token, "Y"],
                    ["documentation", USDC_TYPE.String, doc],
                    ["typeName", USDC_TYPE.Token, "Mesh"],
                ])
            )
        ).toEqual([]);
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

        // In a binary layer assetInfo is a dictionary field, and three.js does not compose the
        // layer's payloads, so neither is collected; its asset attributes are.
        expect(
            extractUsdAssetPaths(
                text("PXR-USDC"),
                fakeUsdcParser([
                    ["assetInfo", USDC_TYPE.Dictionary, { identifier: "assets/Ball/Ball.usd" }],
                    ["payload", USDC_TYPE.PayloadListOp, "./Ball.geom.usd"],
                    ["default", USDC_TYPE.AssetPath, "./Ball_tex.png"],
                ])
            )
        ).toEqual(["./Ball_tex.png"]);
    });

    it("tells sublayers apart from references, in ASCII and binary layers", () => {
        const layer = `#usda 1.0
(
    subLayers = [
        @./look.usda@,
        @shared.usda@
    ]
)
def Xform "A" (
    prepend references = @shared.usda@
)
{
    asset inputs:file = @tex/a.png@
}
`;
        const ascii = collectUsdLayerPaths(text(layer));
        expect(ascii.paths.sort()).toEqual(["./look.usda", "shared.usda", "tex/a.png"]);
        // A layer named as a reference too is not a sublayer-only name.
        expect(ascii.subLayers).toEqual(["./look.usda"]);

        class SubLayerParser {
            tokens = ["subLayers", "sub/layer.usda", "default", "tex/b.png"];
            strings = [1];
            fields = [
                { tokenIndex: 0, valueRep: { typeEnum: USDC_TYPE.StringVector, payload: 0 } },
                { tokenIndex: 2, valueRep: { typeEnum: USDC_TYPE.AssetPath, payload: 0 } },
            ];
            reader = (() => {
                const words = [1, 0, 0];
                let at = 0;
                return {
                    tell: () => at,
                    seek: (offset: number) => {
                        at = offset;
                    },
                    readUint64: () => words[at++],
                    readUint32: () => words[at++],
                };
            })();
            parseData() {
                return { specsByPath: {} };
            }
            _readValue() {
                return "tex/b.png";
            }
        }
        expect(collectUsdLayerPaths(text("PXR-USDC"), SubLayerParser)).toEqual({
            paths: ["tex/b.png", "sub/layer.usda"],
            subLayers: ["sub/layer.usda"],
        });
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

    it("rejects absolute, drive-letter and scheme names written in a nested layer", async () => {
        const requested = installFetch({
            "m/sub/layer.usda":
                "#usda 1.0\n" +
                "prepend references = @/abs/abs.usda@\n" +
                "prepend references = @C:/win/win.usda@\n" +
                "prepend references = @C:\\win\\back.usda@\n" +
                "prepend references = @file:///etc/passwd.usda@\n" +
                "prepend references = @omniverse://host/x.usda@\n" +
                "prepend references = @//host/share/y.usda@\n" +
                "prepend references = @https://example.com/z.usda@\n" +
                "asset inputs:file = @/abs/tex.png@\n" +
                "prepend references = @../../../outside.usda@\n" +
                "prepend references = @deeper/inside.usda@\n",
            "m/sub/deeper/inside.usda": "#usda 1.0\nasset inputs:file = @/abs/deep.png@\n",
        });
        const resolver = newResolver();

        const result = await buildUsdPackage({
            rootKey: "m/root.usda",
            rootBytes: text("#usda 1.0\nprepend references = @sub/layer.usda@\n"),
            resolver,
            fflate: fakeFflate,
        });

        // Only the two in-asset layers are requested, at any depth.
        expect(requested.sort()).toEqual(["m/sub/deeper/inside.usda", "m/sub/layer.usda"]);
        expect(resolver.notLoaded()).toEqual(
            expect.arrayContaining([
                { path: "/abs/abs.usda", reason: "outside-asset" },
                { path: "C:/win/win.usda", reason: "outside-asset" },
                { path: "C:\\win\\back.usda", reason: "outside-asset" },
                { path: "file:///etc/passwd.usda", reason: "external" },
                { path: "omniverse://host/x.usda", reason: "external" },
                { path: "//host/share/y.usda", reason: "outside-asset" },
                { path: "https://example.com/z.usda", reason: "external" },
                { path: "/abs/tex.png", reason: "outside-asset" },
                { path: "../../../outside.usda", reason: "outside-asset" },
                { path: "/abs/deep.png", reason: "outside-asset" },
            ])
        );
        expect(resolver.notLoaded().filter((item) => item.reason === "not-found")).toEqual([]);
        // A rejected image is stored as the placeholder under the composer's key, so the
        // composer never asks for it by URL.
        const content = Object.fromEntries(packageEntries(result.buffer));
        expect(content["sub//abs/tex.png"]).toBeDefined();
        expect(content["sub/deeper//abs/deep.png"]).toBeDefined();
    });

    it("lists sublayers as not shown instead of downloading them, at any depth", async () => {
        const requested = installFetch({
            "m/geom/part.usda":
                "#usda 1.0\n(\n    subLayers = [@./part_overrides.usda@]\n)\n" +
                "asset inputs:file = @tex/wood.png@\n",
            "m/geom/tex/wood.png": "PNG",
            "m/layers/look.usda": "#usda 1.0\n",
            "m/geom/part_overrides.usda": "#usda 1.0\n",
        });
        const resolver = newResolver();

        await buildUsdPackage({
            rootKey: "m/scene.usda",
            rootBytes: text(
                "#usda 1.0\n(\n    subLayers = [\n        @layers/look.usda@,\n" +
                    "        @/abs/look.usda@,\n        @omniverse://host/look.usda@,\n" +
                    "        @../../above.usda@\n    ]\n)\n" +
                    'def Xform "Part" (\n    prepend references = @geom/part.usda@\n)\n{\n}\n'
            ),
            resolver,
            fflate: fakeFflate,
        });

        // References and their textures are still fetched; no sublayer is.
        expect(requested.sort()).toEqual(["m/geom/part.usda", "m/geom/tex/wood.png"]);
        expect(resolver.notLoaded()).toEqual(
            expect.arrayContaining([
                { path: "m/layers/look.usda", reason: "sublayer" },
                { path: "m/geom/part_overrides.usda", reason: "sublayer" },
                // Sublayer names the path rules reject stay listed as rejected.
                { path: "/abs/look.usda", reason: "outside-asset" },
                { path: "omniverse://host/look.usda", reason: "external" },
                { path: "../../above.usda", reason: "outside-asset" },
            ])
        );
        expect(resolver.notLoaded()).toHaveLength(5);
        expect(describeUnloaded([{ path: "m/layers/look.usda", reason: "sublayer" }])).toEqual([
            "m/layers/look.usda (not shown by this viewer (USD sublayers))",
        ]);
    });

    it("downloads sublayers when fetchSubLayers is on, with the path rules applied", async () => {
        const requested = installFetch({ "m/layers/look.usda": "#usda 1.0\n" });
        const resolver = newResolver();

        await buildUsdPackage({
            rootKey: "m/scene.usda",
            rootBytes: text(
                "#usda 1.0\n(\n    subLayers = [@layers/look.usda@, @file:///etc/x.usda@]\n)\n"
            ),
            resolver,
            fflate: fakeFflate,
            fetchSubLayers: true,
        });

        expect(requested).toEqual(["m/layers/look.usda"]);
        expect(resolver.notLoaded()).toEqual([{ path: "file:///etc/x.usda", reason: "external" }]);
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
