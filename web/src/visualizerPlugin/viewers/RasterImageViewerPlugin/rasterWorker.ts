/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Decode worker of the TIFF/TGA viewer. Decoding a large image takes seconds and holds hundreds of
 * megabytes, so it runs here and the page stays responsive; a worker that runs out of memory ends
 * without taking the tab down. The decoders are imported statically: this file is its own chunk and
 * is only fetched when the viewer opens. The worker is same-origin (`worker-src 'self'`), so no CSP
 * change is needed.
 *
 * Protocol (`rasterSession.ts`): the worker first posts `{type: "ready"}`; then `{id, type: "open", buffer, kind, limits}` answers `{id, ok, pages}`;
 * `{id, type: "decode", index}` answers `{id, ok, width, height, data, conversion}`; a failure
 * answers `{id, ok: false, reason, message}`.
 */

import "./pakoGlobal";
import UTIF from "utif2";
import TGA from "tga-js";
import pako from "pako";
import { RasterDecodeError } from "./rasterDecode";
import { RasterEngine } from "./rasterEngine";

const scope = self as unknown as {
    onmessage: ((event: MessageEvent) => void) | null;
    postMessage: (message: unknown, transfer?: Transferable[]) => void;
};

// utif2 and pako are CommonJS: depending on the bundler the API is the namespace or its `default`.
const engine = new RasterEngine({
    utif: ((UTIF as any).default ?? UTIF) as any,
    tga: ((TGA as any).default ?? TGA) as any,
    pako: ((pako as any).default ?? pako) as any,
});

scope.onmessage = (event: MessageEvent) => {
    const { id, type } = event.data;
    try {
        if (type === "open") {
            const pages = engine.open(event.data.buffer, event.data.kind, event.data.limits);
            scope.postMessage({ id, ok: true, pages });
        } else {
            const raster = engine.decode(event.data.index);
            scope.postMessage(
                {
                    id,
                    ok: true,
                    width: raster.width,
                    height: raster.height,
                    data: raster.data,
                    conversion: raster.conversion,
                },
                [raster.data.buffer]
            );
        }
    } catch (error) {
        const known = error instanceof RasterDecodeError;
        scope.postMessage({
            id,
            ok: false,
            reason: known ? error.reason : "corrupt",
            message: known ? error.message : "The image could not be decoded.",
        });
    }
};

// Last statement: tells the page the script loaded and the decoders are ready. The page sends the
// file only after this, so a worker that fails to start leaves the bytes with the page.
scope.postMessage({ type: "ready" });
