/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

/**
 * The robotics viewer's VAMS data sources (web/customInstalls/robotics/overlay/, compiled into the
 * Lichtblick build) handle presigned URLs, which are bearer credentials. These are their pure
 * helpers: the file name stands in for a URL in names and messages, the deep-link URLs are taken
 * out of the object the app saves as a "recent source", and the whole-file download refuses a
 * selection over its limit, naming the file, its size and the limit, before it reads the body.
 */

import {
    DEFAULT_MAX_WHOLE_FILE_BYTES,
    REMOVED_URL,
    downloadWholeFiles,
    extensionOf,
    fileNameFromUrl,
    formatBytes,
    overLimitMessage,
    parseMaxBytes,
    scrubAlert,
    scrubError,
    scrubText,
    takeSignedUrls,
    type DownloadResponse,
} from "../../../../customInstalls/robotics/overlay/vamsSourceSafety";
import { formatBytes as hostFormatBytes, wholeFileOverLimitMessage } from "./roboticsDeepLink";

const SIGNED =
    "https://s3.us-east-1.amazonaws.com/bucket/asset-1/logs/run%201.mcap" +
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIAEXAMPLE%2F20261023%2Fus-east-1" +
    "%2Fs3%2Faws4_request&X-Amz-Security-Token=abc%2B%2F%3D&X-Amz-Signature=deadbeef";
const OTHER_SIGNED = SIGNED.replace("run%201.mcap", "other.mcap");

describe("file names stand in for URLs", () => {
    it("names a presigned URL by its decoded last path segment", () => {
        expect(fileNameFromUrl(SIGNED)).toBe("run 1.mcap");
        expect(fileNameFromUrl("not a url")).toBe("file");
        expect(extensionOf("Run.MCAP")).toBe(".mcap");
        expect(extensionOf("noext")).toBe("");
    });

    it("replaces the selection's URLs with their names, in either spelling", () => {
        const text = `Fetching remote file failed. <${SIGNED}> Status code: 403. / ${decodeURIComponent(
            SIGNED
        )}`;
        const scrubbed = scrubText(text, [SIGNED], ["run 1.mcap"]);
        expect(scrubbed).toBe(
            "Fetching remote file failed. <run 1.mcap> Status code: 403. / run 1.mcap"
        );
    });

    it("removes any other signed URL", () => {
        const scrubbed = scrubText(`GET <${OTHER_SIGNED}> failed`, [SIGNED], ["run 1.mcap"]);
        expect(scrubbed).toBe(`GET <${REMOVED_URL}> failed`);
        expect(scrubbed).not.toContain("X-Amz");
    });

    it("returns an error that carries no URL unchanged", () => {
        const abort = new Error("The operation was aborted");
        abort.name = "AbortError";
        expect(scrubError(abort, [SIGNED], ["run 1.mcap"])).toBe(abort);
    });

    it("rebuilds an error that carries a URL, keeping its name", () => {
        const original = new TypeError(`Failed to fetch: ${SIGNED}`);
        const scrubbed = scrubError(original, [SIGNED], ["run 1.mcap"]) as Error;
        expect(scrubbed).not.toBe(original);
        expect(scrubbed.name).toBe("TypeError");
        expect(scrubbed.message).toBe("Failed to fetch: run 1.mcap");
        expect(String(scrubbed.stack)).not.toContain("X-Amz");
    });

    it("scrubs an alert's message, tip and error", () => {
        const alert = scrubAlert(
            {
                severity: "error",
                message: `GET <${SIGNED}> failed with status 403`,
                tip: `Check ${SIGNED}`,
                error: new Error(`Remote file is missing file size. <${SIGNED}>`),
            },
            [SIGNED],
            ["run 1.mcap"]
        );
        expect(JSON.stringify({ ...alert, error: alert.error?.message })).not.toContain("X-Amz");
        expect(alert.severity).toBe("error");
        expect(alert.message).toBe("GET <run 1.mcap> failed with status 403");
    });
});

describe("takeSignedUrls", () => {
    it("takes the deep link's URLs out of the params the app saves as a recent source", () => {
        const params: Record<string, string | undefined> = {
            url: `${SIGNED},${OTHER_SIGNED}`,
            maxBytes: "5",
        };
        expect(takeSignedUrls(params)).toEqual([SIGNED, OTHER_SIGNED]);
        expect(params).toEqual({ maxBytes: "5" });
        // A repeat call with the same object (a re-run effect) gets the same URLs.
        expect(takeSignedUrls(params)).toEqual([SIGNED, OTHER_SIGNED]);
    });

    it("returns nothing without params", () => {
        expect(takeSignedUrls(undefined)).toEqual([]);
        expect(takeSignedUrls({})).toEqual([]);
    });
});

describe("sizes and the limit", () => {
    it("formats sizes in binary units", () => {
        expect(formatBytes(1073741824)).toBe("1 GiB");
        expect(formatBytes(1610612736)).toBe("1.5 GiB");
        expect(formatBytes(600 * 1024 * 1024)).toBe("600 MiB");
        expect(formatBytes(12)).toBe("12 B");
    });

    it("reads ds.maxBytes, defaulting to 1 GiB", () => {
        expect(parseMaxBytes("2048")).toBe(2048);
        for (const value of [undefined, "", "0", "-5", "lots"]) {
            expect(parseMaxBytes(value)).toBe(DEFAULT_MAX_WHOLE_FILE_BYTES);
        }
    });
});

describe("downloadWholeFiles", () => {
    /** A response whose body yields `chunks`; `read` counts how many chunks were read. */
    const response = (chunks: number[], contentLength?: number, status = 200) => {
        const state = { read: 0, cancelled: false };
        const res: DownloadResponse = {
            ok: status >= 200 && status < 300,
            status,
            headers: {
                get: (name: string) =>
                    name.toLowerCase() === "content-length" && contentLength !== undefined
                        ? String(contentLength)
                        : null,
            },
            body: {
                getReader: () => ({
                    read: async () => {
                        if (state.cancelled || state.read >= chunks.length) {
                            return { done: true };
                        }
                        const size = chunks[state.read];
                        state.read += 1;
                        return { done: false, value: new Uint8Array(size) };
                    },
                    cancel: async () => {
                        state.cancelled = true;
                    },
                }),
            },
        };
        return { res, state };
    };

    const fetchFrom = (responses: Record<string, DownloadResponse>, signals: AbortSignal[] = []) =>
        jest.fn(async (url: string, init: { signal: AbortSignal }) => {
            signals.push(init.signal);
            return responses[url];
        });

    it("downloads every file of the selection, in order", async () => {
        const a = response([10, 20], 30);
        const b = response([5], 5);
        const files = await downloadWholeFiles(
            ["u-a", "u-b"],
            ["a.db3", "b.db3"],
            100,
            fetchFrom({ "u-a": a.res, "u-b": b.res })
        );
        expect(files.map((file) => [file.name, file.size, file.parts.length])).toEqual([
            ["a.db3", 30, 2],
            ["b.db3", 5, 1],
        ]);
    });

    it("refuses an oversized file by its Content-Length before reading the body", async () => {
        const big = response([1024], 2 * 1024 * 1024 * 1024);
        const signals: AbortSignal[] = [];
        await expect(
            downloadWholeFiles(
                ["u-big"],
                ["flight.db3"],
                DEFAULT_MAX_WHOLE_FILE_BYTES,
                fetchFrom({ "u-big": big.res }, signals)
            )
        ).rejects.toThrow(
            "flight.db3 is 2 GiB, larger than the 1 GiB limit for files opened by whole download."
        );
        expect(big.state.read).toBe(0);
        expect(signals[0].aborted).toBe(true);
    });

    it("applies the limit to the selection's total", async () => {
        const a = response([1], 600 * 1024 * 1024);
        const b = response([1], 600 * 1024 * 1024);
        const error = await downloadWholeFiles(
            ["u-a", "u-b"],
            ["run_0.db3", "run_1.db3"],
            DEFAULT_MAX_WHOLE_FILE_BYTES,
            fetchFrom({ "u-a": a.res, "u-b": b.res })
        ).catch((e: Error) => e);
        expect((error as Error).message).toBe(
            "The selected files are 1.2 GiB together (run_0.db3 600 MiB, run_1.db3 600 MiB), " +
                "larger than the 1 GiB limit for files opened by whole download."
        );
        expect(a.state.read + b.state.read).toBe(0);
    });

    it("counts a body without Content-Length and stops at the limit", async () => {
        const unsized = response([40, 40, 40, 40]);
        await expect(
            downloadWholeFiles(["u"], ["flight.ulg"], 100, fetchFrom({ u: unsized.res }))
        ).rejects.toThrow(
            "flight.ulg is larger than the 100 B limit for files opened by whole download."
        );
        expect(unsized.state.read).toBe(3);
        expect(unsized.state.cancelled).toBe(true);
    });

    it("names the file and status of a refused download, never its URL", async () => {
        const denied = response([], 0, 403);
        await expect(
            downloadWholeFiles([SIGNED], ["run.db3"], 100, fetchFrom({ [SIGNED]: denied.res }))
        ).rejects.toThrow("Downloading run.db3 failed with status 403.");
    });

    it("replaces a network error, which names the URL, with the file name", async () => {
        const failing = jest.fn(async () => {
            throw new TypeError(`Failed to fetch: ${SIGNED}`);
        });
        const error = await downloadWholeFiles([SIGNED], ["run.db3"], 100, failing).catch(
            (e: Error) => e
        );
        expect((error as Error).message).toBe(
            "Downloading run.db3 failed: the request did not complete."
        );
        expect((error as Error).message).not.toContain("X-Amz");
    });
});

describe("the host component refuses an over-limit selection in the viewer's words", () => {
    const GiB = 1024 * 1024 * 1024;
    it.each([
        [["over-limit.db3"], [1.1 * GiB], GiB],
        [["run_0.db3", "run_1.db3"], [600 * 1024 * 1024, 700 * 1024 * 1024], GiB],
        [["flight.ulg"], [101], 100],
    ])("%j", (names, sizes, maxBytes) => {
        const host = wholeFileOverLimitMessage(
            names.map((name, i) => ({ name, size: sizes[i] })),
            maxBytes
        );
        expect(host).toBe(overLimitMessage(names, sizes, maxBytes));
    });

    it("formats sizes as the viewer does", () => {
        for (const bytes of [
            0,
            12,
            1023,
            1024,
            1536,
            600 * 1024 * 1024,
            1.1 * 1024 ** 3,
            2 ** 40,
        ]) {
            expect(hostFormatBytes(bytes)).toBe(formatBytes(bytes));
        }
    });

    it("leaves a selection within the limit, or of unknown size, to the viewer", () => {
        expect(wholeFileOverLimitMessage([{ name: "a.db3", size: 100 }], 100)).toBeNull();
        expect(
            wholeFileOverLimitMessage([{ name: "a.db3", size: 2 * GiB }, { name: "b.db3" }], GiB)
        ).toBeNull();
    });
});
