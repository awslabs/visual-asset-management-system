/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Makes the Cesium viewer read every tileset file through a presigned S3 URL (#1630).
 *
 * A tileset is many requests (tileset.json, nested tilesets, subtree files, tiles, glTF buffers
 * and textures), each of them a stream-route URL. The route answers a cross-origin 307 to a
 * presigned S3 URL, which a browser follows with `Origin: null`. An external bucket whose CORS rule
 * lists only the web origin sends no `Access-Control-Allow-Origin` for `null`, so the read fails.
 * A request made straight to the presigned URL carries the web origin and matches that rule.
 *
 * Every Cesium request for a URL is made by `Resource._Implementations.loadWithXhr` (JSON, text,
 * binary, blob and the image blob path) or `Resource._Implementations.createImage` (an image
 * element, or a request without headers). While a viewer is mounted both are wrapped: a URL under
 * that viewer's stream-route prefix is turned into a presign call (the same authorized download
 * route the other viewers use, `getPresignedFileUrl`) and a read of the presigned URL with no
 * `Authorization` header. Any other URL goes to the original function unchanged. Authorization
 * stays in the API: the presign call is decided there and a refusal reaches the user as the API's
 * own answer (a `RequestErrorEvent` carrying its status and message, as the stream route did).
 *
 * Installing is reference counted: each mounted viewer registers a scope (its stream-route
 * prefix); the wrappers go in with the first scope and come out with the last. Cesium is a page
 * global, so two viewers share the one wrapper.
 */

import {
    getPresignedFileUrl,
    PRESIGNED_LINK_UNAVAILABLE_MESSAGE,
    PresignedReadError,
    resolvePresignedFileUrl,
} from "../../core/presignedFetch";
import { isApiRefusalError } from "../../core/streamFailure";

export interface PresignedStreamScope {
    /** `${api}database/{db}/assets/{id}/download/stream/`, as the viewer builds stream URLs. */
    streamPrefix: string;
    databaseId: string;
    assetId: string;
}

interface Installation {
    Cesium: any;
    originalLoadWithXhr: (...args: any[]) => any;
    wrappedLoadWithXhr: (...args: any[]) => any;
    originalCreateImage: (...args: any[]) => any;
    wrappedCreateImage: (...args: any[]) => any;
    scopes: Set<PresignedStreamScope>;
}

let installation: Installation | null = null;

/** Request headers never sent to S3: the API's credential, and a type that forces a preflight. */
const HEADERS_NOT_FORWARDED = new Set(["authorization", "content-type"]);

interface StreamTarget {
    scope: PresignedStreamScope;
    key: string;
    assetVersionId?: string;
}

function parseStreamUrl(url: unknown, scopes: Set<PresignedStreamScope>): StreamTarget | null {
    if (typeof url !== "string") return null;
    for (const scope of scopes) {
        if (!url.startsWith(scope.streamPrefix)) continue;
        const rest = url.slice(scope.streamPrefix.length).split("#")[0];
        const queryStart = rest.indexOf("?");
        const path = queryStart === -1 ? rest : rest.slice(0, queryStart);
        const query = queryStart === -1 ? "" : rest.slice(queryStart + 1);
        if (!path) return null;
        try {
            // The stream URL keeps the slashes and encodes each segment (constructStreamingUrl).
            const key = path
                .split("/")
                .map((segment) => decodeURIComponent(segment))
                .join("/");
            const assetVersionId = new URLSearchParams(query).get("assetVersionId") || undefined;
            // A decoded `.` or `..` segment (for example from `%2e%2e` or `%2F`) is never a key the
            // viewer asks for: Cesium resolves ordinary relative segments before the hook sees the
            // URL. Leave such a URL to the stream route, where the API validates the key.
            if (key.split("/").some((segment) => segment === "." || segment === "..")) {
                return null;
            }
            return { scope, key, assetVersionId };
        } catch {
            return null;
        }
    }
    return null;
}

function forwardedHeaders(headers: unknown): Record<string, string> | undefined {
    if (!headers || typeof headers !== "object") return undefined;
    const kept: Record<string, string> = {};
    for (const [name, value] of Object.entries(headers as Record<string, string>)) {
        if (!HEADERS_NOT_FORWARDED.has(name.toLowerCase())) kept[name] = value;
    }
    return Object.keys(kept).length ? kept : undefined;
}

/**
 * The error a failed presign becomes for Cesium: a `RequestErrorEvent` with the status the API
 * answered and, for its own refusal reason, a JSON body with that `message`, so the viewer words
 * the failure as it did when the stream route answered (tilesetFailure.ts) and its retry
 * callback still sees a 401 or 403.
 */
function presignFailureEvent(Cesium: any, error: any): any {
    // The download route answers a file that is not in the bucket with a `400` naming it
    // (`fileNotFound`). The stream route answered such a tile with a 404, so it is one here: a
    // missing tile is a tile failure Cesium does not retry, not an API refusal shown as text.
    if (error?.fileNotFound === true) return new Cesium.RequestErrorEvent(404);
    const status = typeof error?.status === "number" ? error.status : undefined;
    const body = isApiRefusalError(error) ? JSON.stringify({ message: error.message }) : undefined;
    return status === undefined
        ? new Cesium.RequestErrorEvent()
        : new Cesium.RequestErrorEvent(status, body);
}

function isAbort(error: any): boolean {
    return error?.name === "AbortError";
}

/** What a read of the presigned URL ended with: the original loader's resolve or reject. */
function readOnce(
    install: Installation,
    signedUrl: string,
    args: any[],
    onCancel: (xhr: any) => void
): Promise<{ ok: true; value: any } | { ok: false; error: any }> {
    return new Promise((resolve) => {
        const [, responseType, method, data, headers, , overrideMimeType, ...rest] = args;
        const inner = {
            resolve: (value: any) => resolve({ ok: true, value }),
            reject: (error: any) => resolve({ ok: false, error }),
        };
        const xhr = install.originalLoadWithXhr(
            signedUrl,
            responseType,
            method,
            data,
            forwardedHeaders(headers),
            inner,
            overrideMimeType,
            ...rest
        );
        onCancel(xhr);
    });
}

function wrapLoadWithXhr(install: Installation) {
    return function loadWithXhr(this: unknown, ...args: any[]) {
        const [url, , method, , , deferred] = args;
        const target = parseStreamUrl(url, install.scopes);
        // A signed URL is valid for GET only, so any other method keeps the stream route.
        if (!target || (method && String(method).toUpperCase() !== "GET")) {
            return install.originalLoadWithXhr.apply(this, args);
        }

        const controller = new AbortController();
        let realXhr: any;
        const handle = {
            abort() {
                controller.abort();
                if (realXhr && typeof realXhr.abort === "function") realXhr.abort();
            },
        };
        const request = {
            databaseId: target.scope.databaseId,
            assetId: target.scope.assetId,
            key: target.key,
            assetVersionId: target.assetVersionId,
            signal: controller.signal,
        };
        const subject = `the tileset file "${target.key}"`;

        (async () => {
            try {
                const resolved = await resolvePresignedFileUrl(request, subject);
                let outcome = await readOnce(install, resolved.url, args, (x) => (realXhr = x));
                // A reused URL may have expired: S3 answers 403, so read once more with a new one.
                if (!outcome.ok && outcome.error?.statusCode === 403 && resolved.cached) {
                    const fresh = await getPresignedFileUrl(request, subject, { fresh: true });
                    outcome = await readOnce(install, fresh, args, (x) => (realXhr = x));
                }
                if (controller.signal.aborted) return;
                if (outcome.ok) {
                    deferred.resolve(outcome.value);
                    return;
                }
                const status = outcome.error?.statusCode;
                // S3, not the API, refused: an expired link or a missing file.
                if (status === 403 || status === 404) {
                    deferred.reject(
                        new PresignedReadError(PRESIGNED_LINK_UNAVAILABLE_MESSAGE, status)
                    );
                    return;
                }
                deferred.reject(outcome.error);
            } catch (error) {
                if (controller.signal.aborted || isAbort(error)) return;
                deferred.reject(presignFailureEvent(install.Cesium, error));
            }
        })();

        return handle;
    };
}

function wrapCreateImage(install: Installation) {
    return function createImage(this: unknown, ...args: any[]) {
        const [request, , deferred] = args;
        const target = parseStreamUrl(request?.url, install.scopes);
        if (!target) return install.originalCreateImage.apply(this, args);

        // Cesium cancels an image request through `request.cancelFunction`. Until the presign call
        // returns there is no read to abort, so the cancel stops the presign and keeps the original
        // function from starting a read nobody waits for. The original sets its own cancel function
        // when it starts one.
        const controller = new AbortController();
        request.cancelFunction = () => controller.abort();

        (async () => {
            try {
                const signed = await getPresignedFileUrl(
                    {
                        databaseId: target.scope.databaseId,
                        assetId: target.scope.assetId,
                        key: target.key,
                        assetVersionId: target.assetVersionId,
                        signal: controller.signal,
                    },
                    `the tileset image "${target.key}"`
                );
                if (controller.signal.aborted) return;
                // The original reads `request.url` when called; the request keeps its own URL after.
                const streamUrl = request.url;
                request.url = signed;
                try {
                    const [, ...others] = args;
                    install.originalCreateImage.call(this, request, ...others);
                } finally {
                    request.url = streamUrl;
                }
            } catch (error) {
                if (controller.signal.aborted || isAbort(error)) return;
                deferred.reject(presignFailureEvent(install.Cesium, error));
            }
        })();
    };
}

/**
 * Registers `scope` and, for the first scope, wraps Cesium's request functions. Returns the
 * function that removes the scope and, with the last scope, restores the original functions.
 */
export function installPresignedStreamReads(Cesium: any, scope: PresignedStreamScope): () => void {
    const implementations = Cesium?.Resource?._Implementations;
    if (!implementations) {
        // The bundled Cesium has no hook point: keep the stream route (the 307) rather than fail.
        console.warn("Cesium request hook not available; tiles are read through the stream route");
        return () => undefined;
    }

    if (installation && installation.Cesium !== Cesium) {
        // A different Cesium object (a reload of the library): the old wrappers are not ours to keep.
        installation = null;
    }
    if (!installation) {
        const created: Installation = {
            Cesium,
            originalLoadWithXhr: implementations.loadWithXhr,
            wrappedLoadWithXhr: () => undefined,
            originalCreateImage: implementations.createImage,
            wrappedCreateImage: () => undefined,
            scopes: new Set(),
        };
        created.wrappedLoadWithXhr = wrapLoadWithXhr(created);
        created.wrappedCreateImage = wrapCreateImage(created);
        implementations.loadWithXhr = created.wrappedLoadWithXhr;
        implementations.createImage = created.wrappedCreateImage;
        installation = created;
    }

    const active = installation;
    active.scopes.add(scope);
    let removed = false;
    return () => {
        if (removed) return;
        removed = true;
        active.scopes.delete(scope);
        if (active.scopes.size > 0) return;
        // Restore only what is still ours: another wrapper put on top stays, and passes through.
        if (implementations.loadWithXhr === active.wrappedLoadWithXhr) {
            implementations.loadWithXhr = active.originalLoadWithXhr;
        }
        if (implementations.createImage === active.wrappedCreateImage) {
            implementations.createImage = active.originalCreateImage;
        }
        if (installation === active) installation = null;
    };
}
