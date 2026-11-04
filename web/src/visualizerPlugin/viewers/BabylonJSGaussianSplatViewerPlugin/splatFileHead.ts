/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Identifies the container of a Gaussian splat file from its first bytes, so the BabylonJS viewer can
 * refuse a file its loader cannot read instead of handing it over. The bundled loader opens SPZ
 * versions 2 and 3 (a gzip stream) and PLY. Given anything else it parses the bytes as a raw .splat
 * buffer, which throws outside the load promise and leaves the viewer loading forever.
 */

export const SPLAT_HEAD_BYTES = 16;

export type SplatFileKind =
    | { kind: "spz-gzip" }
    | { kind: "spz-ngsp"; version: number | null }
    | { kind: "ply" }
    | { kind: "unknown" };

export const identifySplatFile = (head: Uint8Array): SplatFileKind => {
    if (head.length >= 2 && head[0] === 0x1f && head[1] === 0x8b) {
        return { kind: "spz-gzip" };
    }
    if (
        head.length >= 4 &&
        head[0] === 0x4e && // N
        head[1] === 0x47 && // G
        head[2] === 0x53 && // S
        head[3] === 0x50 // P
    ) {
        const version =
            head.length >= 8
                ? new DataView(head.buffer, head.byteOffset, head.byteLength).getUint32(4, true)
                : null;
        return { kind: "spz-ngsp", version };
    }
    if (head.length >= 3 && head[0] === 0x70 && head[1] === 0x6c && head[2] === 0x79) {
        return { kind: "ply" };
    }
    return { kind: "unknown" };
};

/** The message shown for a file the viewer cannot open, or null when the loader can read it. */
export const unsupportedSplatMessage = (kind: SplatFileKind, fileName: string): string | null => {
    const name = fileName || "This file";
    switch (kind.kind) {
        case "spz-gzip":
        case "ply":
            return null;
        case "spz-ngsp":
            return (
                `${name} uses SPZ format version ${kind.version ?? "4 or later"}. ` +
                "The BabylonJS Gaussian Splat viewer opens SPZ versions 2 and 3 and PLY files."
            );
        default:
            return (
                `${name} is not an SPZ or PLY Gaussian splat file, ` +
                "so the BabylonJS Gaussian Splat viewer cannot open it."
            );
    }
};

/**
 * Reads the first bytes of a file from a download URL with a range request, or null when they cannot
 * be read (the caller then loads the file without the check). A server that ignores the range still
 * sends the whole body; only the first chunk is read before the stream is cancelled.
 */
export const readFileHead = async (
    url: string,
    byteCount = SPLAT_HEAD_BYTES,
    signal?: AbortSignal
): Promise<Uint8Array | null> => {
    try {
        const response = await fetch(url, {
            headers: { Range: `bytes=0-${byteCount - 1}` },
            signal,
        });
        if (!response.ok || !response.body) return null;
        const reader = response.body.getReader();
        const head = new Uint8Array(byteCount);
        let filled = 0;
        while (filled < byteCount) {
            const { done, value } = await reader.read();
            if (done || !value) break;
            const take = Math.min(value.length, byteCount - filled);
            head.set(value.subarray(0, take), filled);
            filled += take;
        }
        await reader.cancel().catch(() => undefined);
        return head.subarray(0, filled);
    } catch {
        return null;
    }
};
