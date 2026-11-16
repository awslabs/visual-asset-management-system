/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * One opened TIFF or TGA: its pages, and a way to decode each. The work runs in the decode worker
 * when the browser can start one, and on the page otherwise (a unit test, a browser without module
 * workers, or a worker whose script cannot be loaded). A worker that stops after it started (out of
 * memory, a crash) rejects what it was asked with a {@link RasterDecodeError}, which the viewer turns
 * into its fallback notice.
 */

import { RasterImageDependencyManager } from "./dependencies";
import { createRasterWorker } from "./createRasterWorker";
import { DecodedRaster, RasterDecodeError, RasterKind, RasterLimits } from "./rasterDecode";
import { PageSummary, RasterEngine } from "./rasterEngine";

export interface RasterSession {
    pages: PageSummary[];
    decode(index: number): Promise<DecodedRaster>;
    /** Ends the worker and drops the file. A request still in flight is rejected. */
    dispose(): void;
}

const WORKER_STOPPED =
    "The image decoder stopped unexpectedly, which usually means the image is too large for this browser.";
const SESSION_CLOSED = "The image viewer was closed.";

/** How long a worker may take to report it has started before the page decodes instead. */
export const WORKER_STARTUP_TIMEOUT_MS = 15_000;

type Pending = { resolve: (value: any) => void; reject: (error: Error) => void };

/**
 * Waits for the worker to report `ready` (the last statement of its module). Resolves false when its
 * script cannot be loaded or started: a blocked or missing file (a stale tab after a redeploy, an
 * extension), a browser without module workers, or a worker that never answers. Nothing has been
 * sent to it yet, so the file's bytes are still here for the in-page decoder.
 */
function workerStarted(worker: Worker, timeoutMs: number): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
        const done = (ok: boolean) => {
            clearTimeout(timer);
            worker.onmessage = null;
            worker.onerror = null;
            worker.onmessageerror = null;
            resolve(ok);
        };
        const timer = setTimeout(() => done(false), timeoutMs);
        worker.onmessage = (event: MessageEvent) => {
            if (event.data?.type === "ready") done(true);
        };
        worker.onerror = (event) => {
            event.preventDefault?.();
            done(false);
        };
        worker.onmessageerror = () => done(false);
    });
}

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
        const entries = Array.from(pending.values());
        pending.clear();
        entries.forEach((entry) => entry.reject(error));
    };
    worker.onmessage = (event: MessageEvent) => {
        const message = event.data;
        const entry = pending.get(message?.id);
        if (!entry) return;
        pending.delete(message.id);
        if (message.ok) entry.resolve(message);
        else entry.reject(new RasterDecodeError(message.reason, message.message));
    };
    // After `ready`, a worker that errors or dies mid-request ran out of memory or crashed.
    worker.onerror = (event) => {
        event.preventDefault?.();
        failAll(new RasterDecodeError("unsupported", WORKER_STOPPED));
    };
    worker.onmessageerror = () => failAll(new RasterDecodeError("unsupported", WORKER_STOPPED));

    const request = (message: Record<string, unknown>, transfer: Transferable[] = []) =>
        new Promise<any>((resolve, reject) => {
            if (ended) {
                reject(new RasterDecodeError("unsupported", SESSION_CLOSED));
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
            if (ended) return;
            ended = true;
            // A request still in flight (a page switch at unmount) is rejected, not left hanging.
            failAll(new RasterDecodeError("unsupported", SESSION_CLOSED));
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

async function inPageSession(
    buffer: ArrayBuffer,
    kind: RasterKind,
    limits: RasterLimits
): Promise<RasterSession> {
    await RasterImageDependencyManager.loadInline();
    const engine = new RasterEngine({
        utif: RasterImageDependencyManager.getUtif(),
        tga: RasterImageDependencyManager.getTga(),
        pako: RasterImageDependencyManager.getPako(),
    });
    // The same engine, caps and checks the worker runs.
    const pages = engine.open(buffer, kind, limits);
    return {
        pages,
        decode: async (index) => engine.decode(index),
        dispose: () => undefined,
    };
}

/**
 * Opens `buffer`; throws {@link RasterDecodeError} for a file that cannot be shown. The decode worker
 * is used when it starts; when it cannot be created or its script cannot be loaded the page decodes
 * instead, so a blocked or stale worker file never turns a small image into an error.
 */
export async function openRasterSession(
    buffer: ArrayBuffer,
    kind: RasterKind,
    limits: RasterLimits,
    startupTimeoutMs = WORKER_STARTUP_TIMEOUT_MS
): Promise<RasterSession> {
    const worker = createRasterWorker();
    if (worker) {
        if (await workerStarted(worker, startupTimeoutMs)) {
            return workerSession(worker, buffer, kind, limits);
        }
        console.warn("TIFF/TGA viewer: decode worker did not start, decoding on the page");
        worker.terminate();
    }
    return inPageSession(buffer, kind, limits);
}
