/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The deep link is the only channel a presigned URL takes into the framed robotics viewer, so it
 * must carry the URL after `#` only (never in a query string a server logs) and reach the app
 * byte-for-byte: S3 refuses a SigV4 URL whose percent-encoding changed in transit.
 */

import {
    DEFAULT_BASE_PATH,
    DEFAULT_MAX_WHOLE_FILE_BYTES,
    MULTI_FILE_DB3,
    buildDeepLink,
    checkSelection,
    fileExtension,
    maxWholeFileBytes,
    parseDeepLink,
    roboticsBasePath,
    sourceForExtension,
} from "./roboticsDeepLink";

// Shaped like a path-style SigV4 presigned URL with a session token: literal %2F, + and = in the
// query values, which a double decode or a form-encoding pass would corrupt.
const SIGNED =
    "https://s3.us-east-1.amazonaws.com/asset-bucket/asset-1/logs/run%201.mcap" +
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256" +
    "&X-Amz-Credential=ASIAEXAMPLE%2F20261023%2Fus-east-1%2Fs3%2Faws4_request" +
    "&X-Amz-Date=20261023T020000Z&X-Amz-Expires=86400" +
    "&X-Amz-Security-Token=IQoJb3JpZ2luX2VjE%2B%2Fab%3D%3Dcd+ef%2Bgh%3D" +
    "&X-Amz-SignedHeaders=host" +
    "&X-Amz-Signature=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

const signedFor = (name: string) => SIGNED.replace("run%201.mcap", encodeURIComponent(name));

/** How the framed app reads the link: its entrypoint moves the fragment into the query string. */
const asTheAppReadsIt = (src: string) => {
    const fragment = new URL(src, "https://vams.example").hash.slice(1);
    return new URL(`https://vams.example${DEFAULT_BASE_PATH}?${fragment}`).searchParams;
};

describe("buildDeepLink", () => {
    it("puts every signed URL after # and nothing in the base path's query", () => {
        const src = buildDeepLink(DEFAULT_BASE_PATH, "vams-remote", [SIGNED]);
        const [beforeHash, afterHash] = src.split("#");
        expect(beforeHash).toBe(DEFAULT_BASE_PATH);
        expect(beforeHash).not.toContain("?");
        expect(beforeHash).not.toContain("X-Amz");
        expect(afterHash).toContain("X-Amz-Signature");
    });

    it("hands a SigV4 URL to the app byte-for-byte", () => {
        const src = buildDeepLink(DEFAULT_BASE_PATH, "vams-remote", [SIGNED]);
        const params = asTheAppReadsIt(src);
        expect(params.get("ds")).toBe("vams-remote");
        expect(params.getAll("ds.url")).toEqual([SIGNED]);
        expect(parseDeepLink(src).urls).toEqual([SIGNED]);
    });

    it("[control] a value placed in the fragment unencoded does not survive the app's parse", () => {
        // Positive control for the round trip above: without the one encodeURIComponent per value,
        // URLSearchParams turns "+" into a space and splits the URL at its own "&".
        const src = `${DEFAULT_BASE_PATH}#ds=vams-remote&ds.url=${SIGNED}`;
        expect(asTheAppReadsIt(src).getAll("ds.url")).not.toEqual([SIGNED]);
    });

    it("keeps the order of repeated ds.url values", () => {
        const urls = ["c.mcap", "a.mcap", "b.mcap"].map(signedFor);
        const src = buildDeepLink(DEFAULT_BASE_PATH, "vams-remote", urls);
        expect(asTheAppReadsIt(src).getAll("ds.url")).toEqual(urls);
    });

    it("adds the whole-file limit for the download source only", () => {
        const download = buildDeepLink(DEFAULT_BASE_PATH, "vams-download", [signedFor("a.db3")], 5);
        expect(asTheAppReadsIt(download).get("ds.maxBytes")).toBe("5");
        const remote = buildDeepLink(DEFAULT_BASE_PATH, "vams-remote", [SIGNED], 5);
        expect(asTheAppReadsIt(remote).has("ds.maxBytes")).toBe(false);
    });
});

describe("source per extension", () => {
    it.each([
        [".mcap", "vams-remote"],
        [".bag", "vams-remote"],
        [".db3", "vams-download"],
        [".ulg", "vams-download"],
        [".ulog", "vams-download"],
        [".MCAP", "vams-remote"],
    ])("%s opens with %s", (extension, source) => {
        expect(sourceForExtension(extension)).toBe(source);
    });

    it("opens nothing else", () => {
        expect(sourceForExtension(".glb")).toBeUndefined();
        expect(sourceForExtension("")).toBeUndefined();
    });

    it("reads the extension from the key's last segment", () => {
        expect(fileExtension("/folder.v2/Run.MCAP")).toBe(".mcap");
        expect(fileExtension("/folder.v2/noext")).toBe("");
    });
});

describe("checkSelection", () => {
    it.each([
        [["/a.mcap"], "vams-remote"],
        [["/a.mcap", "/b.mcap", "/c.mcap"], "vams-remote"],
        [["/a.bag"], "vams-remote"],
        [["/a.db3"], "vams-download"],
        [["/a.ulg"], "vams-download"],
        [["/a.ulog"], "vams-download"],
    ])("opens %j with %s", (keys, source) => {
        const check = checkSelection(keys);
        expect(check).toEqual(expect.objectContaining({ ok: true, source }));
    });

    it.each([
        [["/a.mcap", "/b.bag"], /different types/],
        [["/a.db3", "/b.ulg"], /different types/],
        [["/a.bag", "/b.bag"], /ROS 1 bag .* one file at a time/],
        [["/a.ulg", "/b.ulg"], /PX4 ULog .* one file at a time/],
        [["/a.ulg", "/b.ulog"], /PX4 ULog .* one file at a time/],
        [["/a.glb"], /does not open a\.glb/],
        [[], /No file/],
    ])("refuses %j", (keys, message) => {
        const check = checkSelection(keys);
        expect(check.ok).toBe(false);
        expect(!check.ok && check.message).toMatch(message);
    });

    it("opens the files of a ROS 2 split bag together only while MULTI_FILE_DB3 is set", () => {
        expect(checkSelection(["/a_0.db3", "/a_1.db3"]).ok).toBe(MULTI_FILE_DB3);
        expect(checkSelection(["/a_0.db3", "/a_1.db3"], false).ok).toBe(false);
        expect(checkSelection(["/a_0.db3", "/a_1.db3"], true).ok).toBe(true);
    });
});

describe("customParameters", () => {
    it("defaults the whole-file limit to 1 GiB", () => {
        expect(DEFAULT_MAX_WHOLE_FILE_BYTES).toBe(1073741824);
        expect(maxWholeFileBytes(undefined)).toBe(DEFAULT_MAX_WHOLE_FILE_BYTES);
        expect(maxWholeFileBytes({})).toBe(DEFAULT_MAX_WHOLE_FILE_BYTES);
    });

    it("takes a positive maxWholeFileBytes override", () => {
        expect(maxWholeFileBytes({ maxWholeFileBytes: 52428800 })).toBe(52428800);
        expect(maxWholeFileBytes({ maxWholeFileBytes: "2048" })).toBe(2048);
    });

    it("ignores an override that is not a positive number", () => {
        for (const value of [0, -1, "lots", null, Number.NaN]) {
            expect(maxWholeFileBytes({ maxWholeFileBytes: value })).toBe(
                DEFAULT_MAX_WHOLE_FILE_BYTES
            );
        }
    });

    it("takes the base path from customParameters, else the hosting default", () => {
        expect(roboticsBasePath({ basePath: "/custom/index.html" })).toBe("/custom/index.html");
        expect(roboticsBasePath(undefined)).toBe(DEFAULT_BASE_PATH);
    });
});
