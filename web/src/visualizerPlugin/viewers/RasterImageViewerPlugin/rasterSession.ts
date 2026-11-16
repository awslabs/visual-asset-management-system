/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * One opened TIFF or TGA: its pages, and a way to decode each. The work runs in the decode worker
 * when the browser can start one, and on the page otherwise (a unit test, or a worker that cannot
 * be created). A worker that stops unexpectedly (out of memory, a crash) rejects what it was asked
 * with a {@link RasterDecodeError}, which the viewer turns into its fallback notice.
 */

import { RasterImageDependencyManager } from "./dependencies";
import { createRasterWorker } from "./createRasterWorker";
import { DecodedRaster, RasterDecodeError, RasterKind, RasterLimits } from "./rasterDecode";
import { PageSummary, RasterEngine } from "./rasterEngine";

export interface RasterSession {
    pages: PageSummary[];
    decode(index: number): Promise<DecodedRaster>;
    /** Ends the worker and drops the file. Pending requests are abandoned. */
    dispose(): void;
}

const WORKER_STOPPED =
    "The image decoder stopped unexpectedly, which usually means the image is too large for this browser.";

type Pending = { resolve: (value: any) => void; reject: (error: Error) => void };

function workerSession(
    worker: Worker,
    buffer: ArrayBuffer,
    kind: RasterKind,
    limits: RasterLimits
): Promise<RasterSession> {
    let nextId = 1;
    let ended = false;
    const pending = new Map<number, Pending>();

    const failAll = (error: Error) => {
        pending.forEach((entry) => entry.reject(error));
        pending.clear();
    };
    worker.onmessage = (event: MessageEvent) => {
        const message = event.data;
        const entry = pending.get(message.id);
        if (!entry) return;
        pending.delete(message.id);
        if (message.ok) entry.resolve(message);
        else entry.reject(new RasterDecodeError(message.reason, message.message));
    };
    worker.onerror = (event) => {
        event.preventDefault?.();
        failAll(new RasterDecodeError("unsupported", WORKER_STOPPED));
    };
    worker.onmessageerror = () => failAll(new RasterDecodeError("unsupported", WORKER_STOPPED));

    const request = (message: Record<string, unknown>, transfer: Transferable[] = []) =>
        new Promise<any>((resolve, reject) => {
            if (ended) {
                reject(new RasterDecodeError("unsupported", WORKER_STOPPED));
                return;
            }
            const id = nextId++;
            pending.set(id, { resolve, reject });
            worker.postMessage({ id, ...message }, transfer);
        });

    const session: RasterSession = {
        pages: [],
        decode: async (index) => {
            const reply = await request({ type: "decode", index });
            return {
                width: reply.width,
                height: reply.height,
                data: reply.data,
                conversion: reply.conversion,
            };
        },
        dispose: () => {
            ended = true;
            pending.clear();
            worker.terminate();
        },
    };
    return request({ type: "open", buffer, kind, limits }, [buffer]).then(
        (reply) => {
            session.pages = reply.pages;
            return session;
        },
        (error) => {
            session.dispose();
            throw error;
        }
    );
}

/** Opens `buffer`; throws {@link RasterDecodeError} for a file that cannot be shown. */
export async function openRasterSession(
    buffer: ArrayBuffer,
    kind: RasterKind,
    limits: RasterLimits
): Promise<RasterSession> {
    const worker = createRasterWorker();
    if (worker) return workerSession(worker, buffer, kind, limits);

    await RasterImageDependencyManager.loadInline();
    const engine = new RasterEngine({
        utif: RasterImageDependencyManager.getUtif(),
        tga: RasterImageDependencyManager.getTga(),
        pako: RasterImageDependencyManager.getPako(),
    });
    const pages = engine.open(buffer, kind, limits);
    return {
        pages,
        decode: async (index) => engine.decode(index),
        dispose: () => undefined,
    };
}
