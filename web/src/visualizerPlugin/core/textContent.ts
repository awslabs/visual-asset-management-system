/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Text-or-binary check and bounded loader shared by the text-style viewers (Text Viewer, Text Diff
 * Viewer). Pure and free of the registry so it can be unit tested like `streamFailure.ts`.
 *
 * A text viewer is offered by NAME (extension, or none at all), so the bytes it receives can still be
 * an image, an archive or a compiled binary. Before anything reaches the highlighter the loader reads
 * at most the first {@link TEXT_SAMPLE_BYTES} bytes, classifies them, and stops there when they are
 * not text: a binary file is never downloaded whole and never rendered.
 */

/** How much of a file is read before deciding whether it is text (git's 8 KB rule). */
export const TEXT_SAMPLE_BYTES = 8192;

/** A sample with more than this share of control bytes is binary. */
export const CONTROL_BYTE_RATIO_LIMIT = 0.1;

/** The stricter share applied when the sample is also not valid UTF-8. */
export const CONTROL_BYTE_RATIO_LIMIT_INVALID_UTF8 = 0.02;

/**
 * NUL bytes on one byte position of each pair, and almost none on the other, mark UTF-16 without a
 * byte-order mark (ASCII text has a 0x00 high byte on every character). At least this share of the
 * pairs must carry the NUL on the dominant side...
 */
export const UTF16_NUL_SIDE_MIN_RATIO = 0.3;

/** ...and at most this share on the other side... */
export const UTF16_NUL_OTHER_SIDE_MAX_RATIO = 0.02;

/** ...with at least this many NUL bytes on the dominant side. */
export const UTF16_MIN_NULS = 4;

/** Encodings the text viewers decode. */
export type TextEncodingLabel = "utf-8" | "utf-16le" | "utf-16be";

/** What a sample of a file's first bytes is. */
export type TextSampleClass =
    | { kind: "text"; encoding: TextEncodingLabel }
    | { kind: "binary" }
    | { kind: "unsupported-encoding" };

/** The outcome of loading a file for a text viewer. */
export type TextLoadResult =
    | { kind: "text"; text: string; encoding: TextEncodingLabel }
    | { kind: "binary" }
    | { kind: "unsupported-encoding" };

/** A refused request, carrying the HTTP status so a viewer can classify it (401/403, 404, 410). */
export class TextFetchError extends Error {
    status?: number;
    constructor(message: string, status?: number) {
        super(message);
        this.name = "TextFetchError";
        this.status = status;
    }
}

const startsWith = (bytes: Uint8Array, prefix: number[]): boolean =>
    bytes.length >= prefix.length && prefix.every((value, index) => bytes[index] === value);

/**
 * A control byte that text does not normally contain: C0 bytes other than TAB, LF, VT, FF, CR,
 * backspace (0x08, overstrike in man-page output) and ESC (0x1B, ANSI colours in logs), plus DEL.
 * NUL is decided separately.
 */
const isSuspectControlByte = (byte: number): boolean =>
    (byte >= 0x01 && byte <= 0x07) ||
    (byte >= 0x0e && byte <= 0x1a) ||
    (byte >= 0x1c && byte <= 0x1f) ||
    byte === 0x7f;

const isValidUtf8 = (bytes: Uint8Array, isComplete: boolean): boolean => {
    try {
        // `stream: true` for a partial sample: a multi-byte character cut at the sample edge is held
        // back as incomplete rather than reported as invalid.
        new TextDecoder("utf-8", { fatal: true }).decode(bytes, { stream: !isComplete });
        return true;
    } catch {
        return false;
    }
};

/** True when the NUL bytes sit on one side of each byte pair, as UTF-16 text without a BOM has. */
const looksLikeBomlessUtf16 = (bytes: Uint8Array): boolean => {
    const pairs = Math.floor(bytes.length / 2);
    if (pairs === 0) return false;
    let evenNuls = 0;
    let oddNuls = 0;
    for (let index = 0; index < pairs * 2; index++) {
        if (bytes[index] === 0) {
            if (index % 2 === 0) evenNuls++;
            else oddNuls++;
        }
    }
    const dominantCount = Math.max(evenNuls, oddNuls);
    // A stray NUL in a few bytes of text is binary (git's rule), not a pattern.
    if (dominantCount < UTF16_MIN_NULS) return false;
    const dominant = dominantCount / pairs;
    const other = Math.min(evenNuls, oddNuls) / pairs;
    return dominant >= UTF16_NUL_SIDE_MIN_RATIO && other <= UTF16_NUL_OTHER_SIDE_MAX_RATIO;
};

/**
 * Classify a sample of a file's first bytes. `isComplete` says the sample is the whole file.
 *
 * In order:
 *  1. A byte-order mark decides the encoding: UTF-32 (either order) is unsupported, UTF-16 LE/BE is
 *     text (and its NUL bytes are expected), UTF-8 continues to the checks below.
 *  2. Any NUL byte without a UTF-16 BOM is binary (PNG, ZIP, gzip, ELF, nearly all compressed
 *     data), except that NULs on one side of each byte pair are UTF-16 without a BOM: unsupported.
 *  3. The share of suspect control bytes: binary above {@link CONTROL_BYTE_RATIO_LIMIT}, or above
 *     {@link CONTROL_BYTE_RATIO_LIMIT_INVALID_UTF8} when the sample is not valid UTF-8. Invalid
 *     UTF-8 with few control bytes (Latin-1 text) still renders, with replacement characters.
 *  4. An empty file is text.
 */
export function classifyTextSample(bytes: Uint8Array, isComplete: boolean): TextSampleClass {
    if (bytes.length === 0) return { kind: "text", encoding: "utf-8" };

    // UTF-32 first: its little-endian BOM begins with the UTF-16 little-endian one.
    if (
        startsWith(bytes, [0x00, 0x00, 0xfe, 0xff]) ||
        startsWith(bytes, [0xff, 0xfe, 0x00, 0x00])
    ) {
        return { kind: "unsupported-encoding" };
    }
    if (startsWith(bytes, [0xff, 0xfe])) return { kind: "text", encoding: "utf-16le" };
    if (startsWith(bytes, [0xfe, 0xff])) return { kind: "text", encoding: "utf-16be" };

    if (bytes.includes(0x00)) {
        return looksLikeBomlessUtf16(bytes) ? { kind: "unsupported-encoding" } : { kind: "binary" };
    }

    let suspect = 0;
    for (const byte of bytes) {
        if (isSuspectControlByte(byte)) suspect++;
    }
    const ratio = suspect / bytes.length;
    const limit = isValidUtf8(bytes, isComplete)
        ? CONTROL_BYTE_RATIO_LIMIT
        : CONTROL_BYTE_RATIO_LIMIT_INVALID_UTF8;
    if (ratio > limit) return { kind: "binary" };

    return { kind: "text", encoding: "utf-8" };
}

/** Decode a whole file in the encoding the sample showed. A matching BOM is stripped. */
export function decodeText(bytes: Uint8Array, encoding: TextEncodingLabel): string {
    return new TextDecoder(encoding).decode(bytes);
}

const concat = (chunks: Uint8Array[], length: number): Uint8Array => {
    const out = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
        out.set(chunk, offset);
        offset += chunk.length;
    }
    return out;
};

/** The object size from a `Content-Range: bytes 0-8191/12345` header, when the server gives it. */
const totalFromContentRange = (header: string | null | undefined): number | undefined => {
    const match = /\/(\d+)\s*$/.exec(header || "");
    return match ? Number(match[1]) : undefined;
};

const ensureOk = (response: Response): void => {
    if (!response.ok) {
        throw new TextFetchError(`HTTP error! status: ${response.status}`, response.status);
    }
};

/** Finish a classified sample: the notice when it is not text, else the decoded file. */
const finish = (
    sample: TextSampleClass,
    whole: () => Promise<Uint8Array>
): Promise<TextLoadResult> =>
    sample.kind === "text"
        ? whole().then((bytes) => ({
              kind: "text" as const,
              text: decodeText(bytes, sample.encoding),
              encoding: sample.encoding,
          }))
        : Promise.resolve(sample);

/**
 * A full (200) response, read through its body stream: the first {@link TEXT_SAMPLE_BYTES} are
 * classified before the rest is read, and a binary file's transfer is cancelled there. Used when the
 * server ignored the `Range` header, or on the retry without it.
 */
const readFullResponse = async (response: Response): Promise<TextLoadResult> => {
    const reader = response.body?.getReader?.();
    if (!reader) {
        // No readable stream to stop early (older runtimes): classify the first bytes of the whole body.
        const bytes = new Uint8Array(await response.arrayBuffer());
        const sample = classifyTextSample(
            bytes.subarray(0, TEXT_SAMPLE_BYTES),
            bytes.length <= TEXT_SAMPLE_BYTES
        );
        return finish(sample, async () => bytes);
    }

    const chunks: Uint8Array[] = [];
    let length = 0;
    let done = false;
    while (length < TEXT_SAMPLE_BYTES) {
        const next = await reader.read();
        if (next.done) {
            done = true;
            break;
        }
        chunks.push(next.value);
        length += next.value.length;
    }
    const head = concat(chunks, length);
    const sample = classifyTextSample(head.subarray(0, TEXT_SAMPLE_BYTES), done);
    if (sample.kind !== "text") {
        // Stop the transfer: none of the remaining bytes is ever read or rendered.
        await reader.cancel().catch(() => undefined);
        return sample;
    }
    return finish(sample, async () => {
        while (!done) {
            const next = await reader.read();
            if (next.done) {
                done = true;
            } else {
                chunks.push(next.value);
                length += next.value.length;
            }
        }
        return concat(chunks, length);
    });
};

/**
 * Load a file for a text viewer from its presigned URL (from `downloadAsset`, called once by the
 * viewer, so audit and authorization are unchanged).
 *
 *  - Asks for the first {@link TEXT_SAMPLE_BYTES} with `Range`. A 206 is classified; when it already
 *    holds the whole file it is decoded at once, when the file is larger and text one full GET
 *    follows, and when it is not text nothing more is requested.
 *  - A 200 (the server ignored `Range`) is read as a stream, classified on its first bytes and
 *    cancelled when it is not text.
 *  - A 416 is what Amazon S3 answers for a range on an empty object: empty text.
 *  - A network `TypeError` (a bucket whose CORS rules refuse the `Range` preflight) is retried once
 *    without `Range` and read as a 200.
 *  - Any other refusal throws {@link TextFetchError} with the status. An abort rethrows as-is.
 */
export async function loadTextFile(
    url: string,
    signal?: AbortSignal,
    fetchImpl: typeof fetch = (...args) => fetch(...args)
): Promise<TextLoadResult> {
    let response: Response;
    try {
        response = await fetchImpl(url, {
            signal,
            headers: { Range: `bytes=0-${TEXT_SAMPLE_BYTES - 1}` },
        });
    } catch (error) {
        if (signal?.aborted || !(error instanceof TypeError)) throw error;
        const retry = await fetchImpl(url, { signal });
        ensureOk(retry);
        return readFullResponse(retry);
    }

    if (response.status === 416) return { kind: "text", text: "", encoding: "utf-8" };
    ensureOk(response);
    if (response.status !== 206) return readFullResponse(response);

    const head = new Uint8Array(await response.arrayBuffer());
    const total = totalFromContentRange(response.headers?.get?.("Content-Range"));
    const isComplete = total !== undefined ? total <= head.length : head.length < TEXT_SAMPLE_BYTES;
    const sample = classifyTextSample(head.subarray(0, TEXT_SAMPLE_BYTES), isComplete);
    return finish(sample, async () => {
        if (isComplete) return head;
        const full = await fetchImpl(url, { signal });
        ensureOk(full);
        return new Uint8Array(await full.arrayBuffer());
    });
}
