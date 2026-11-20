/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Pure helpers that interpret the Miris stream status endpoint
 * (`GET /addon/miris/asset-status`) for the stream viewer.
 */

/** File types the Miris Spatial Streaming Upload workflow accepts (its input file filter). */
const MIRIS_UPLOAD_EXTENSIONS = [".usd", ".usda", ".usdc", ".usdz"];

/** States the status endpoint reports itself; any other value is a Miris processing state. */
export const STATE_NOT_STREAMED = "notStreamed";
export const STATE_INVALID_UUID = "invalidUuid";
export const STATE_NOT_BOUND = "notBound";
export const STATE_UNKNOWN = "unknown";

const STATE_STREAMABLE = "streamable";
const MIRIS_FAILURE_STATES = ["error", "failed"];

/**
 * What the viewer does for a status `state`:
 *   - notStreamed: the file records no Miris asset; show the "Not yet streamed" guidance
 *   - invalidUuid: the recorded UUID is malformed; show an error
 *   - notBound:    the recorded Miris asset is missing, or lacks the `vams-asset-{assetId}` or
 *                  `vams-database-{databaseId}` tag that binds it to this VAMS asset; show an error
 *   - failed:      Miris reports a definitive processing failure; show an error
 *   - stream:      open the stream (`streamable`, or `unknown` when Miris could not be
 *                  queried; the probe is advisory and never blocks streaming)
 *   - processing:  Miris is still preparing the asset; show the overlay and poll
 */
export type StreamDisposition =
    | "notStreamed"
    | "invalidUuid"
    | "notBound"
    | "failed"
    | "stream"
    | "processing";

export function streamDisposition(state: string): StreamDisposition {
    if (state === STATE_NOT_STREAMED) return "notStreamed";
    if (state === STATE_INVALID_UUID) return "invalidUuid";
    if (state === STATE_NOT_BOUND) return "notBound";
    if (MIRIS_FAILURE_STATES.includes(state)) return "failed";
    if (state === STATE_STREAMABLE || state === STATE_UNKNOWN) return "stream";
    return "processing";
}

/**
 * `filePath` for the status request: the viewed file's object key with a leading slash. The
 * viewer host passes the full S3 key, and the endpoint reduces it against the asset's own root
 * key, so this holds for every bucket layout (default, `baseAssetsPrefix`, existing-key assets).
 */
export function toStatusFilePath(assetKey: string): string {
    return `/${assetKey.replace(/^\/+/, "")}`;
}

/** True when the Miris Spatial Streaming Upload workflow accepts the file at this path or key. */
export function acceptsMirisUpload(path: string): boolean {
    const lower = path.toLowerCase();
    return MIRIS_UPLOAD_EXTENSIONS.some((extension) => lower.endsWith(extension));
}

/** A file version id that identifies an S3 object version ("null" marks an unversioned read). */
export function isKnownVersionId(versionId: string | null | undefined): versionId is string {
    return !!versionId && versionId !== "null";
}

/**
 * True when the stream was built from a different version of the file than the one being
 * viewed. An unrecorded source version (typical of hand-edited metadata) or an unknown viewed
 * version is never stale.
 */
export function isStaleStream(
    sourceVersionId: string | null | undefined,
    viewedVersionId: string | null | undefined
): boolean {
    if (!isKnownVersionId(sourceVersionId) || !isKnownVersionId(viewedVersionId)) return false;
    return sourceVersionId !== viewedVersionId;
}

/**
 * Current S3 version id from a file-info response (`includeVersions: true`): the version
 * flagged `isLatest`, else the first listed.
 */
export function latestVersionIdFromFileInfo(fileInfo: unknown): string | undefined {
    let info = fileInfo as any;
    if (typeof info === "string") {
        try {
            info = JSON.parse(info);
        } catch {
            return undefined;
        }
    }
    const versions: any[] = Array.isArray(info?.versions) ? info.versions : [];
    const latest = versions.find((version) => version?.isLatest) ?? versions[0];
    const versionId = latest?.versionId ?? info?.versionId;
    return isKnownVersionId(versionId) ? versionId : undefined;
}
