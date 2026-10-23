/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAMS-authored helpers for the robotics viewer build, copied into the Lichtblick clone by
 * web/customInstalls/robotics/roboticsViewerInstall.js. This file imports nothing, so the VAMS web
 * unit tests load it directly.
 *
 * A presigned Amazon S3 URL is a bearer credential: anyone holding it can read the object until it
 * expires. These helpers keep it out of what the app shows and stores. The file name stands in for
 * the URL in player names and messages, the deep-link parameters are taken out of the object the
 * app later saves as a "recent source", and the whole-file download refuses a selection over its
 * size limit before it reads the body.
 */

/** Replaces a signed URL that is not one of the selection's own (which become their file names). */
export const REMOVED_URL = "[signed URL removed]";

/** Any http(s) URL carrying an AWS Signature Version 4 query parameter. */
const SIGNED_URL_PATTERN = /https?:\/\/[^\s"'<>`]*X-Amz-[^\s"'<>`]*/gi;

/** The size limit for a whole-file download when the deep link names none: 1 GiB. */
export const DEFAULT_MAX_WHOLE_FILE_BYTES = 1024 * 1024 * 1024;

/** The file name a presigned URL points at: the last segment of its path, decoded. */
export function fileNameFromUrl(url: string): string {
    let pathname: string;
    try {
        pathname = new URL(url).pathname;
    } catch {
        return "file";
    }
    const segment =
        pathname
            .split("/")
            .filter((part) => part.length > 0)
            .pop() ?? "";
    try {
        return decodeURIComponent(segment) || "file";
    } catch {
        return segment || "file";
    }
}

/** The lower-case extension of a file name, dot included ("" when it has none). */
export function extensionOf(name: string): string {
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

const takenUrls = new WeakMap<object, string[]>();

/**
 * Reads the deep link's signed URLs (`ds.url`, repeated values joined with "," by the app's URL
 * parser) and removes them from `params`.
 *
 * The app's player manager calls a data source's `initialize` with this same object and, once it
 * returns, saves `params.url` and `params` as a "recent source" in IndexedDB whenever `params.url`
 * is set. Taking the URLs out here keeps them out of browser storage. A repeat call with the same
 * object (a re-run effect) gets the URLs it already took.
 */
export function takeSignedUrls(params: Record<string, string | undefined> | undefined): string[] {
    if (params == undefined) {
        return [];
    }
    const taken = takenUrls.get(params);
    if (taken) {
        return taken;
    }
    const urls = (params.url ?? "").split(",").filter((url) => url.length > 0);
    delete params.url;
    takenUrls.set(params, urls);
    return urls;
}

/** Every spelling of a URL a message may carry: as given, and percent-decoded. */
function spellings(url: string): string[] {
    const forms = [url];
    for (const decode of [decodeURI, decodeURIComponent]) {
        try {
            const decoded = decode(url);
            if (!forms.includes(decoded)) {
                forms.push(decoded);
            }
        } catch {
            // Not decodable: the given spelling is the only one.
        }
    }
    return forms;
}

/**
 * Replaces each of the selection's URLs in `text` with its file name, then any other signed URL
 * with REMOVED_URL. Upstream error text quotes the URL it failed on ("GET <url> failed with status
 * 403", "Remote file is missing file size. <url>"), and the app shows that text to the user.
 */
export function scrubText(text: string, urls: readonly string[], names: readonly string[]): string {
    let result = text;
    urls.forEach((url, index) => {
        const name = names[index] ?? REMOVED_URL;
        for (const form of spellings(url)) {
            result = result.split(form).join(name);
        }
    });
    return result.replace(SIGNED_URL_PATTERN, REMOVED_URL);
}

/**
 * Returns the error unchanged when neither its message nor its stack carries a URL, else a new
 * Error with the scrubbed message and the original name (so `name === "AbortError"` checks hold).
 */
export function scrubError(
    error: unknown,
    urls: readonly string[],
    names: readonly string[]
): unknown {
    if (!(error instanceof Error)) {
        return typeof error === "string" ? scrubText(error, urls, names) : error;
    }
    const message = scrubText(error.message, urls, names);
    const stack = error.stack ?? "";
    if (message === error.message && scrubText(stack, urls, names) === stack) {
        return error;
    }
    const scrubbed = new Error(message);
    scrubbed.name = error.name;
    return scrubbed;
}

/** The fields of an app alert that carry text. */
export interface AlertText {
    message: string;
    tip?: string;
    error?: Error;
}

/** A copy of `alert` with its message, tip and error scrubbed. */
export function scrubAlert<T extends AlertText>(
    alert: T,
    urls: readonly string[],
    names: readonly string[]
): T {
    const scrubbed: T = { ...alert, message: scrubText(alert.message, urls, names) };
    if (alert.tip != undefined) {
        scrubbed.tip = scrubText(alert.tip, urls, names);
    }
    if (alert.error != undefined) {
        const error = scrubError(alert.error, urls, names);
        scrubbed.error = error instanceof Error ? error : new Error(String(error));
    }
    return scrubbed;
}

/** "1 GiB", "1.5 GiB", "600 MiB", "12 B". */
export function formatBytes(bytes: number): string {
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    const rounded = unit === 0 || value >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
    return `${rounded} ${units[unit] ?? "B"}`;
}

/** The deep link's `ds.maxBytes`, or the default when it is absent or not a positive number. */
export function parseMaxBytes(value: string | undefined): number {
    const parsed = value == undefined || value.trim() === "" ? NaN : Number(value);
    return Number.isFinite(parsed) && parsed > 0
        ? Math.floor(parsed)
        : DEFAULT_MAX_WHOLE_FILE_BYTES;
}

/** The parts of a Fetch API Response the whole-file download reads. */
export interface DownloadResponse {
    ok: boolean;
    status: number;
    headers: { get(name: string): string | null };
    body: {
        getReader(): {
            read(): Promise<{ done: boolean; value?: Uint8Array }>;
            cancel(reason?: unknown): Promise<void>;
        };
    } | null;
}

/** The fetch function the whole-file download calls (the global fetch in the app). */
export type FetchLike = (
    url: string,
    init: { signal: AbortSignal; cache: "no-store" }
) => Promise<DownloadResponse>;

/** One downloaded file: its name and the chunks of its body, in order. */
export interface DownloadedFile {
    name: string;
    parts: Uint8Array[];
    size: number;
}

const LIMIT_REASON = "limit for files opened by whole download";

function parseContentLength(value: string | null): number | undefined {
    if (value == null || !/^\d+$/.test(value.trim())) {
        return undefined;
    }
    return Number(value.trim());
}

/** "a.db3 is 1.2 GiB, ..." for one file; the total and every size for several. */
function overLimitMessage(
    names: readonly string[],
    sizes: ReadonlyArray<number | undefined>,
    maxBytes: number
): string {
    const known = sizes.map((size, index) => ({ name: names[index] ?? "", size }));
    const total = known.reduce((sum, file) => sum + (file.size ?? 0), 0);
    if (names.length === 1) {
        return `${names[0] ?? "The file"} is ${formatBytes(total)}, larger than the ${formatBytes(
            maxBytes
        )} ${LIMIT_REASON}.`;
    }
    const listed = known
        .filter((file) => file.size != undefined)
        .map((file) => `${file.name} ${formatBytes(file.size ?? 0)}`)
        .join(", ");
    const atLeast = known.every((file) => file.size != undefined) ? "" : "at least ";
    return `The selected files are ${atLeast}${formatBytes(
        total
    )} together (${listed}), larger than the ${formatBytes(maxBytes)} ${LIMIT_REASON}.`;
}

/** The message for a body that passed the limit while it was read (no usable Content-Length). */
function overLimitWhileReadingMessage(names: readonly string[], maxBytes: number): string {
    const subject = names.length === 1 ? `${names[0] ?? "The file"} is` : "The selected files are";
    return `${subject} larger than the ${formatBytes(maxBytes)} ${LIMIT_REASON}.`;
}

/**
 * Downloads every URL whole, refusing a selection larger than `maxBytes` in total.
 *
 * The responses' Content-Length headers are compared with the limit before any body is read, so an
 * oversized selection is refused without being downloaded. A body without the header is counted as
 * it streams and the download stops at the limit. On any failure every request is aborted. Error
 * messages name the file, its size and the limit, never the URL.
 */
export async function downloadWholeFiles(
    urls: readonly string[],
    names: readonly string[],
    maxBytes: number,
    fetchImpl: FetchLike
): Promise<DownloadedFile[]> {
    const controller = new AbortController();
    const nameOf = (index: number) => names[index] ?? `file ${index + 1}`;
    try {
        const responses = await Promise.all(
            urls.map(async (url, index) => {
                let response: DownloadResponse;
                try {
                    response = await fetchImpl(url, {
                        signal: controller.signal,
                        cache: "no-store",
                    });
                } catch {
                    throw new Error(
                        `Downloading ${nameOf(index)} failed: the request did not complete.`
                    );
                }
                if (!response.ok) {
                    throw new Error(
                        `Downloading ${nameOf(index)} failed with status ${response.status}.`
                    );
                }
                return response;
            })
        );

        const declared = responses.map((response) =>
            parseContentLength(response.headers.get("content-length"))
        );
        const declaredTotal = declared.reduce<number>((sum, size) => sum + (size ?? 0), 0);
        if (declaredTotal > maxBytes) {
            throw new Error(
                overLimitMessage(
                    urls.map((_url, index) => nameOf(index)),
                    declared,
                    maxBytes
                )
            );
        }

        let received = 0;
        const files: DownloadedFile[] = [];
        for (const [index, response] of responses.entries()) {
            if (response.body == null) {
                throw new Error(`Downloading ${nameOf(index)} failed: the response had no body.`);
            }
            const reader = response.body.getReader();
            const parts: Uint8Array[] = [];
            let size = 0;
            for (;;) {
                const { done, value } = await reader.read();
                if (done) {
                    break;
                }
                if (value == undefined) {
                    continue;
                }
                size += value.byteLength;
                received += value.byteLength;
                if (received > maxBytes) {
                    await reader.cancel().catch(() => undefined);
                    throw new Error(
                        overLimitWhileReadingMessage(
                            urls.map((_url, i) => nameOf(i)),
                            maxBytes
                        )
                    );
                }
                parts.push(value);
            }
            files.push({ name: nameOf(index), parts, size });
        }
        return files;
    } catch (error) {
        controller.abort();
        throw error;
    }
}
